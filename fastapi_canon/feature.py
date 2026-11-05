from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from inspect import iscoroutine, iscoroutinefunction
from typing import TYPE_CHECKING, Any

from dishka import AsyncContainer, Provider, Scope, make_async_container
from dishka.entities.key import DependencyKey
from dishka.exceptions import DishkaError  # type: ignore[attr-defined]  # not exported
from fastapi import APIRouter, FastAPI
from starlette.routing import Match, Route, WebSocketRoute, compile_path
from starlette.types import ExceptionHandler, Lifespan
from starlette.types import Scope as ASGIScope

from fastapi_canon.contract_routes import iter_contract_routes
from fastapi_canon.diagnostics import (
    CheckContext,
    CheckIssue,
    CheckReport,
    CheckResult,
    CompositionInfo,
    FeatureCheck,
    OperationInfo,
)
from fastapi_canon.error import ErrorConfigurationError, ErrorRegistry
from fastapi_canon.error.contracts import INSTALLED_REGISTRY_STATE_KEY
from fastapi_canon.error.openapi import install_policy_contracts
from fastapi_canon.error.types import (
    FeatureConfigurationError as FeatureConfigurationError,
)
from fastapi_canon.introspection import _describe, _operation_headers, _qualified_name
from fastapi_canon.openapi import (
    install_openapi_contracts,
    validate_openapi_contracts,
)
from fastapi_canon.policy import RouterPolicy
from fastapi_canon.request_logging import install_request_logging
from fastapi_canon.router import CanonRouter
from fastapi_canon.scopes import _install_scopes

if TYPE_CHECKING:
    from fastapi_canon.integrations.mcp import McpMount, McpOptions
    from fastapi_canon.integrations.mcp.configuration import McpRuntime
    from fastapi_canon.integrations.opentelemetry import Instrumentation, Telemetry


type FeatureLifespan = Lifespan[FastAPI]
type ProviderFactory = Callable[[], Provider]
type ProviderSource = Provider | ProviderFactory
type RouterFactory = Callable[[], APIRouter]

_INSTALLATION_STATE_KEY = "_fastapi_canon_installation"
_DEFAULT_TYPE_BASE = "urn:fastapi-canon:problem"


@dataclass(frozen=True, slots=True, init=False)
class ExceptionHandlerSpec:
    """Associate an exception class with a Starlette exception handler."""

    exception: type[Exception]
    handler: ExceptionHandler

    def __init__(self, exception: type[Exception], handler: ExceptionHandler) -> None:
        raw_exception: object = exception
        raw_handler: object = handler
        if not isinstance(raw_exception, type) or not issubclass(
            raw_exception, Exception
        ):
            msg = "exception must be an Exception subclass"
            raise FeatureConfigurationError(msg)
        if not callable(raw_handler):
            msg = "handler must be callable"
            raise FeatureConfigurationError(msg)
        object.__setattr__(self, "exception", raw_exception)
        object.__setattr__(self, "handler", raw_handler)


@dataclass(frozen=True, slots=True, init=False)
class Feature:
    """An immutable declaration of one feature's FastAPI contributions."""

    name: str
    routers: tuple[APIRouter, ...]
    providers: tuple[ProviderSource, ...]
    instrumentation: tuple[Instrumentation, ...]
    mcp: tuple[McpMount, ...]
    errors: ErrorRegistry | None
    exception_handlers: tuple[ExceptionHandlerSpec, ...]
    lifespan: FeatureLifespan | None
    checks: tuple[FeatureCheck, ...]

    def __init__(
        self,
        *,
        name: str,
        routers: Sequence[APIRouter] = (),
        providers: Sequence[ProviderSource] = (),
        instrumentation: Sequence[Instrumentation] = (),
        mcp: Sequence[McpMount] = (),
        errors: ErrorRegistry | None = None,
        exception_handlers: Sequence[ExceptionHandlerSpec] = (),
        lifespan: FeatureLifespan | None = None,
        checks: Sequence[FeatureCheck] = (),
    ) -> None:
        normalized_checks = tuple(checks)
        if not all(
            callable(check)
            and not iscoroutinefunction(check)
            and not iscoroutinefunction(type(check).__call__)
            for check in normalized_checks
        ):
            raise FeatureConfigurationError(
                "checks must contain synchronous callable functions"
            )
        object.__setattr__(self, "checks", normalized_checks)
        normalized_mcp = tuple(mcp)
        if normalized_mcp:
            from fastapi_canon.integrations.mcp import McpMount

            normalized_mcp = _unique_instances(normalized_mcp, McpMount, "mcp")
        object.__setattr__(self, "mcp", normalized_mcp)
        normalized_name = _validate_feature_name(name)
        normalized_routers = _unique_instances(
            routers, APIRouter, f"feature {normalized_name!r} routers"
        )
        normalized_providers = _unique_provider_sources(
            providers, owner=f"feature {normalized_name!r} providers"
        )
        normalized_instrumentation = tuple(instrumentation)
        if normalized_instrumentation:
            from fastapi_canon.integrations.opentelemetry import Instrumentation

            normalized_instrumentation = _unique_instances(
                normalized_instrumentation,
                Instrumentation,
                f"feature {normalized_name!r} instrumentation",
            )
        normalized_handlers = _unique_instances(
            exception_handlers,
            ExceptionHandlerSpec,
            f"feature {normalized_name!r} exception_handlers",
        )
        raw_errors: object = errors
        raw_lifespan: object = lifespan
        if raw_errors is not None and not isinstance(raw_errors, ErrorRegistry):
            msg = f"feature {normalized_name!r} errors must be an ErrorRegistry instance or None"
            raise FeatureConfigurationError(msg)
        if raw_lifespan is not None and not callable(raw_lifespan):
            msg = f"feature {normalized_name!r} lifespan must be callable or None"
            raise FeatureConfigurationError(msg)

        router_errors = _collect_router_errors(
            normalized_routers, feature_name=normalized_name
        )
        resolved_errors = _merge_errors(
            tuple(
                (normalized_name, registry)
                for registry in (errors, router_errors)
                if registry is not None
            ),
            name=normalized_name,
            type_base=None,
        )

        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "routers", normalized_routers)
        object.__setattr__(self, "providers", normalized_providers)
        object.__setattr__(self, "instrumentation", normalized_instrumentation)
        object.__setattr__(self, "errors", resolved_errors)
        object.__setattr__(self, "exception_handlers", normalized_handlers)
        object.__setattr__(self, "lifespan", lifespan)


@dataclass(frozen=True, slots=True, init=False)
class ErrorOptions:
    """Application-wide options for composing feature error registries."""

    type_base: str | None
    registry_name: str | None
    include_validation_error: bool
    include_http_exceptions: bool
    include_unhandled_error: bool

    def __init__(
        self,
        *,
        type_base: str | None = None,
        registry_name: str | None = "application",
        include_validation_error: bool = True,
        include_http_exceptions: bool = True,
        include_unhandled_error: bool = True,
    ) -> None:
        try:
            validated = ErrorRegistry(
                errors=[], type_base=type_base, name=registry_name
            )
        except ErrorConfigurationError as error:
            raise FeatureConfigurationError(str(error)) from error
        _validate_bool(include_validation_error, "include_validation_error")
        _validate_bool(include_http_exceptions, "include_http_exceptions")
        _validate_bool(include_unhandled_error, "include_unhandled_error")
        object.__setattr__(self, "type_base", validated.type_base)
        object.__setattr__(self, "registry_name", validated.name)
        object.__setattr__(
            self,
            "include_validation_error",
            include_validation_error,
        )
        object.__setattr__(self, "include_http_exceptions", include_http_exceptions)
        object.__setattr__(self, "include_unhandled_error", include_unhandled_error)


@dataclass(frozen=True, slots=True, init=False)
class Composition:
    """An ordered, reusable declaration of an application's features."""

    features: tuple[Feature, ...]
    errors: ErrorOptions
    router_factory: RouterFactory | None
    router_policy: RouterPolicy | None
    telemetry: Telemetry | None
    request_logging: bool
    mcp: McpOptions | None
    overrides: tuple[ProviderSource, ...]

    def __init__(
        self,
        *,
        features: Sequence[Feature] = (),
        errors: ErrorOptions | None = None,
        router_factory: RouterFactory | None = None,
        router_policy: RouterPolicy | None = None,
        telemetry: Telemetry | None = None,
        request_logging: bool = False,
        mcp: McpOptions | None = None,
    ) -> None:
        if mcp is not None:
            from fastapi_canon.integrations.mcp import McpOptions

            if not isinstance(mcp, McpOptions):
                raise FeatureConfigurationError("mcp must be McpOptions or None")
        object.__setattr__(self, "mcp", mcp)
        _validate_bool(request_logging, "request_logging")
        object.__setattr__(self, "request_logging", request_logging)
        if telemetry is not None:
            from fastapi_canon.integrations.opentelemetry import Telemetry

            if not isinstance(telemetry, Telemetry):
                raise FeatureConfigurationError(
                    "telemetry must be a Telemetry instance or None"
                )
        object.__setattr__(self, "telemetry", telemetry)
        normalized_features = _unique_instances(features, Feature, "features")
        _validate_unique_feature_names(normalized_features)
        raw_errors: object = errors
        raw_router_factory: object = router_factory
        raw_router_policy: object = router_policy
        if raw_errors is not None and not isinstance(raw_errors, ErrorOptions):
            msg = "errors must be an ErrorOptions instance or None"
            raise FeatureConfigurationError(msg)
        if raw_router_factory is not None and not callable(raw_router_factory):
            msg = "router_factory must be callable or None"
            raise FeatureConfigurationError(msg)
        if raw_router_policy is not None and not isinstance(
            raw_router_policy, RouterPolicy
        ):
            msg = "router_policy must be a RouterPolicy instance or None"
            raise FeatureConfigurationError(msg)
        object.__setattr__(self, "features", normalized_features)
        object.__setattr__(self, "errors", errors or ErrorOptions())
        object.__setattr__(self, "router_factory", router_factory)
        object.__setattr__(self, "router_policy", router_policy)
        object.__setattr__(self, "overrides", ())

    def build(self, app: FastAPI) -> FastAPI:
        """Install this composition into *app*, validate it and return the app.

        Building the same composition again only repeats validation.
        """
        _apply_composition(app, self)
        _raise_check_errors(self.check(app))
        # Regenerate OpenAPI so routes added since an earlier build are included.
        app.openapi_schema = None
        validate_openapi_contracts(app)
        return app

    def override(self, *providers: ProviderSource) -> Composition:
        """Return a copy whose *providers* replace existing Dishka bindings.

        Overrides are added after every feature provider, so their bindings win.
        Each override may only provide types that a feature already provides.
        """
        if not providers:
            raise FeatureConfigurationError("override expects at least one provider")
        overrides = _unique_provider_sources(
            (*self.overrides, *providers), owner="Composition overrides"
        )
        copy = object.__new__(Composition)
        for name in Composition.__dataclass_fields__:
            object.__setattr__(copy, name, getattr(self, name))
        object.__setattr__(copy, "overrides", overrides)
        return copy

    def check(self, app: FastAPI) -> CheckReport:
        """Check configuration without starting lifespans or resolving services."""
        if not isinstance(app, FastAPI):
            raise FeatureConfigurationError("app must be a FastAPI instance")
        installed = getattr(app.state, _INSTALLATION_STATE_KEY, None)
        if installed is not None:
            try:
                _validate_applied_composition(app, self)
            except FeatureConfigurationError as error:
                return CheckReport(
                    (
                        CheckIssue(
                            "canon.composition.E001",
                            str(error),
                            "Use the composition installed on this application.",
                        ),
                    ),
                    (CheckResult("installation", "failed"),),
                )
            assert isinstance(installed, _Installation)
            return _check_installed(app, installed.description, self.features)
        return _prepare_composition(app, self).report

    def inspect(self, app: FastAPI) -> CompositionInfo:
        """Return the detached HTTP snapshot recorded at installation time."""
        _validate_applied_composition(app, self)
        return _installed_description(app)


@dataclass(frozen=True, slots=True)
class _Installation:
    features: tuple[Feature, ...] = field(compare=False)
    feature_ids: tuple[int, ...]
    errors: ErrorOptions
    router_factory: RouterFactory | None = field(compare=False)
    router_factory_id: int | None
    router_policy: RouterPolicy | None = field(compare=False)
    router_policy_id: int | None
    telemetry: Telemetry | None = field(compare=False)
    telemetry_id: int | None
    request_logging: bool
    mcp: McpOptions | None
    override_ids: tuple[int, ...]
    container: AsyncContainer | None = field(compare=False)
    description: CompositionInfo | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class _Preparation:
    report: CheckReport
    routers: tuple[APIRouter, ...]
    registry: ErrorRegistry | None
    container: AsyncContainer | None
    policy_operations: frozenset[str]
    description: CompositionInfo | None
    missing_dependency: ModuleNotFoundError | None
    mcp_runtime: McpRuntime | None


@dataclass(frozen=True, slots=True)
class _MaterializedProvider:
    feature_name: str
    provider: Provider


class _WebSocketPathError(FeatureConfigurationError):
    def __init__(self, issue: CheckIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def _apply_composition(app: FastAPI, composition: Composition) -> None:
    raw_app: object = app
    if not isinstance(raw_app, FastAPI):
        msg = "app must be a FastAPI instance"
        raise FeatureConfigurationError(msg)
    requested = _Installation(
        mcp=composition.mcp,
        request_logging=composition.request_logging,
        telemetry=composition.telemetry,
        telemetry_id=id(composition.telemetry)
        if composition.telemetry is not None
        else None,
        features=composition.features,
        feature_ids=tuple(id(feature) for feature in composition.features),
        errors=composition.errors,
        router_factory=composition.router_factory,
        router_factory_id=(
            id(composition.router_factory)
            if composition.router_factory is not None
            else None
        ),
        override_ids=tuple(id(source) for source in composition.overrides),
        container=None,
        router_policy=composition.router_policy,
        router_policy_id=(
            id(composition.router_policy)
            if composition.router_policy is not None
            else None
        ),
    )
    installed = getattr(app.state, _INSTALLATION_STATE_KEY, None)
    if installed is not None:
        if not isinstance(installed, _Installation) or installed != requested:
            msg = "a different composition is already applied to this application"
            raise FeatureConfigurationError(msg)
        return

    prepared = _prepare_composition(app, composition)
    if prepared.missing_dependency is not None:
        raise prepared.missing_dependency
    _raise_check_errors(prepared.report)
    if prepared.description is None:
        raise FeatureConfigurationError("Composition description is unavailable")
    telemetry = composition.telemetry
    mounts = tuple(mount for feature in composition.features for mount in feature.mcp)
    handler_specs = _flatten_handlers(composition.features)
    lifespans = tuple(
        feature.lifespan
        for feature in composition.features
        if feature.lifespan is not None
    )
    installed_routers = prepared.routers
    registry = prepared.registry
    if registry is None:
        raise FeatureConfigurationError("Composed error catalog is unavailable")
    policy = composition.router_policy
    container = prepared.container
    policy_operations = prepared.policy_operations

    if telemetry is not None:
        from fastapi_canon.integrations.opentelemetry.native import install

        install(app, telemetry)
    original_lifespan = app.router.lifespan_context
    if container is not None:
        _install_scopes(app, container)
    for router in installed_routers:
        app.include_router(router)
    for spec in handler_specs:
        app.add_exception_handler(spec.exception, spec.handler)
    registry.install(
        app,
        include_validation_error=composition.errors.include_validation_error,
        include_http_exceptions=composition.errors.include_http_exceptions,
        include_unhandled_error=composition.errors.include_unhandled_error,
    )
    if policy is not None and policy.raises:
        install_policy_contracts(app, policy.raises, policy_operations)
    if composition.mcp is not None:
        from fastapi_canon.integrations.mcp.runtime import install_mcp

        if prepared.mcp_runtime is None:
            raise FeatureConfigurationError("MCP runtime configuration is unavailable")
        lifespans = (
            *lifespans,
            install_mcp(app, mounts, composition.mcp, container, prepared.mcp_runtime),
        )
    app.router.lifespan_context = _compose_lifespan(
        original_lifespan,
        # include_router merged these into the app context after we captured
        # original_lifespan. Retain them inside the dependency lifetime.
        (*lifespans, *_router_lifespans(original_lifespan, installed_routers)),
        container,
    )

    if composition.request_logging:
        install_request_logging(app)

    completed = _Installation(
        mcp=composition.mcp,
        request_logging=composition.request_logging,
        telemetry=composition.telemetry,
        telemetry_id=id(composition.telemetry)
        if composition.telemetry is not None
        else None,
        features=composition.features,
        feature_ids=requested.feature_ids,
        errors=composition.errors,
        router_factory=composition.router_factory,
        router_factory_id=requested.router_factory_id,
        router_policy=policy,
        router_policy_id=requested.router_policy_id,
        override_ids=requested.override_ids,
        container=container,
        description=prepared.description,
    )
    setattr(app.state, _INSTALLATION_STATE_KEY, completed)


def _prepare_composition(app: FastAPI, composition: Composition) -> _Preparation:
    issues: list[CheckIssue] = []
    checks: list[CheckResult] = []
    missing_dependencies: list[ModuleNotFoundError] = []
    names = tuple(feature.name for feature in composition.features)
    participants = {
        "providers": tuple(
            feature.name
            for feature in composition.features
            if feature.providers or feature.instrumentation
        ),
        "handlers": tuple(
            feature.name
            for feature in composition.features
            if feature.exception_handlers
        ),
        "http.routers": tuple(
            feature.name for feature in composition.features if feature.routers
        ),
        "errors": tuple(
            feature.name
            for feature in composition.features
            if feature.errors is not None
        ),
    }

    def attempt[ResultT](
        name: str, code: str, hint: str, action: Callable[[], ResultT]
    ) -> tuple[ResultT | None, bool]:
        try:
            result = action()
        except (ErrorConfigurationError, ModuleNotFoundError) as error:
            if isinstance(error, ModuleNotFoundError):
                missing_dependencies.append(error)
            if isinstance(error, _WebSocketPathError):
                issue = error.issue
            else:
                issue = CheckIssue(
                    code,
                    str(error),
                    hint,
                    feature_names=participants.get(name, names),
                    locations=tuple(
                        _qualified_name(source)
                        for feature in composition.features
                        for source in feature.providers
                    )
                    if name == "providers"
                    else (),
                )
            issues.append(issue)
            checks.append(CheckResult(name, "failed"))
            return None, False
        checks.append(CheckResult(name, "passed"))
        return result, True

    attempt(
        "installation",
        "canon.composition.E001",
        "Install before startup and OpenAPI generation; check integration options.",
        lambda: _validate_runtime_options(app, composition),
    )
    routers, routers_ok = attempt(
        "http.routers",
        "canon.routing.E001",
        "Contribute each router once and return a fresh empty application router.",
        lambda: _prepare_feature_routers(app, composition),
    )
    if routers_ok and routers is not None and composition.mcp is not None:
        mcp_path = composition.mcp.path
        attempt(
            "mcp.routes",
            "canon.mcp.E001",
            "Move the conflicting HTTP route or choose a different MCP path.",
            lambda: _validate_mcp_routes(app, routers, mcp_path),
        )
    _, handlers_ok = attempt(
        "handlers",
        "canon.handlers.E001",
        "Keep one handler per exception across the application and its features.",
        lambda: _validate_handlers(app, composition.features),
    )
    registry, registry_ok = attempt(
        "errors",
        "canon.errors.E001",
        "Use compatible error definitions and one explicit application type base.",
        lambda: _prepare_registry(app, composition),
    )
    container, container_ok = attempt(
        "providers",
        "canon.providers.E001",
        "Check the named provider sources and supply the missing Dishka bindings.",
        lambda: _prepare_container(app, composition),
    )
    if routers_ok and routers is not None and container_ok:
        _check_rpc_dependencies(
            app, composition.features, routers, container, issues, checks
        )
    else:
        checks.append(CheckResult("rpc.method_dependencies", "skipped"))
    mcp_runtime = None
    if composition.mcp is not None and not missing_dependencies:
        from fastapi_canon.integrations.mcp.configuration import prepare_runtime

        mcp_options = composition.mcp
        mcp_runtime, _ = attempt(
            "mcp.runtime",
            "canon.mcp.E002",
            "Use synchronous factories that borrow the APP container without starting resources.",
            lambda: prepare_runtime(mcp_options, container),
        )
    policy_operations: frozenset[str] = frozenset()
    description = None
    if routers_ok and registry_ok and handlers_ok and routers is not None:
        description, described = attempt(
            "http.description",
            "canon.description.E001",
            "Ensure declared HTTP errors are present in the composed catalog.",
            lambda: _describe(
                tuple(
                    (
                        feature.name,
                        feature.routers,
                        tuple(_qualified_name(p) for p in feature.providers),
                    )
                    for feature in composition.features
                ),
                routers,
                registry,
                composition.router_policy,
                app.router,
            ),
        )
        if described and description is not None:
            _check_operations(
                (*_operation_headers(app.router), *description.operations),
                issues,
                checks,
            )
            _check_http_paths(
                app.router, routers, composition.features, (), issues, checks
            )
        if not any(
            issue.code in {"canon.routing.E002", "canon.routing.E003"}
            for issue in issues
        ):
            operations, _ = attempt(
                "http.contracts",
                "canon.contracts.E001",
                "Match route response contracts to the composed error catalog.",
                lambda: _validate_error_installation(
                    app,
                    routers,
                    _flatten_handlers(composition.features),
                    registry,
                    include_validation_error=composition.errors.include_validation_error,
                    include_http_exceptions=composition.errors.include_http_exceptions,
                    include_unhandled_error=composition.errors.include_unhandled_error,
                    router_policy=composition.router_policy,
                ),
            )
            policy_operations = operations or frozenset()
        else:
            checks.append(CheckResult("http.contracts", "skipped"))
    else:
        checks.extend(
            (
                CheckResult("http.contracts", "skipped"),
                CheckResult("http.description", "skipped"),
            )
        )
    if description is not None:
        _run_feature_checks(composition.features, description, issues, checks)
    elif any(feature.checks for feature in composition.features):
        checks.append(CheckResult("feature.checks", "skipped"))
    return _Preparation(
        CheckReport(tuple(issues), tuple(checks)),
        routers or (),
        registry,
        container,
        policy_operations,
        description,
        missing_dependencies[0] if missing_dependencies else None,
        mcp_runtime,
    )


def _validate_runtime_options(app: FastAPI, composition: Composition) -> None:
    if app.middleware_stack is not None:
        raise FeatureConfigurationError("install features before the app starts")
    if app.openapi_schema is not None:
        raise FeatureConfigurationError(
            "install features before generating or caching OpenAPI"
        )
    mounts = tuple(mount for feature in composition.features for mount in feature.mcp)
    if mounts or composition.mcp is not None:
        try:
            from fastapi_canon.integrations.mcp.runtime import validate_mounts
        except ModuleNotFoundError as error:
            if error.name != "fastmcp":
                raise
            raise FeatureConfigurationError(
                "Install fastapi-canon[mcp] to enable MCP"
            ) from error
        validate_mounts(mounts, composition.mcp)
        if app.middleware_stack is not None:
            raise FeatureConfigurationError("install MCP before the app starts")
    if composition.request_logging and app.middleware_stack is not None:
        raise FeatureConfigurationError("install request logging before the app starts")
    if composition.telemetry is not None:
        composition.telemetry._prepare(app)


def _prepare_feature_routers(
    app: FastAPI, composition: Composition
) -> tuple[APIRouter, ...]:
    _reject_duplicate_contributions(composition.features, "routers", "router")
    return _prepare_routers(
        _flatten_routers(composition.features),
        composition.router_factory,
        composition.router_policy,
        lambda prefix: _validate_websocket_paths(app, composition.features, prefix),
    )


def _validate_websocket_paths(
    app: FastAPI, features: tuple[Feature, ...], prefix: str
) -> None:
    sources = [("<application>", "", app.router)]
    sources.extend(
        (feature.name, app.router.prefix + prefix, router)
        for feature in features
        for router in feature.routers
    )
    seen: dict[str, tuple[str, str, str]] = {}
    for owner, shared_prefix, router in sources:
        for path, route in iter_contract_routes(router.routes, include_mounts=False):
            if not isinstance(getattr(route, "original_route", route), WebSocketRoute):
                continue
            path = shared_prefix + path
            # Keep converter behavior while erasing declaration-only parameter names.
            pattern = compile_path(shared_prefix + route.path)[0].pattern
            key = re.sub(r"\(\?P<[^>]+>", "(?:", pattern)
            name = route.name
            location = f"WEBSOCKET {path} ({name})"
            previous = seen.get(key)
            if previous is not None:
                raise _WebSocketPathError(
                    CheckIssue(
                        "canon.routing.E004",
                        f"Duplicate WebSocket path {path!r}: {previous[0]!r} "
                        f"({previous[2]}) and {owner!r} ({name})",
                        "Use distinct effective WebSocket paths or remove the duplicate contribution.",
                        feature_names=tuple(dict.fromkeys((previous[0], owner))),
                        locations=(previous[1], location),
                    )
                )
            seen[key] = (owner, location, name)


def _validate_mcp_routes(
    app: FastAPI, routers: tuple[APIRouter, ...], path: str
) -> None:
    for router in (app.router, *routers):
        for route in router.routes:
            for endpoint in (path, path + "/"):
                for method in ("GET", "POST", "DELETE"):
                    scope: ASGIScope = {
                        "type": "http",
                        "path": endpoint,
                        "root_path": "",
                        "method": method,
                    }
                    match, _ = route.matches(scope)
                    if match is Match.FULL or (
                        endpoint == path and match is Match.PARTIAL
                    ):
                        raise FeatureConfigurationError(
                            f"MCP path {path!r} conflicts with an existing route "
                            f"matching {method} {endpoint}"
                        )


def _prepare_registry(app: FastAPI, composition: Composition) -> ErrorRegistry:
    registries = tuple(
        (feature.name, feature.errors)
        for feature in composition.features
        if feature.errors is not None
    )
    policy = composition.router_policy
    if policy is not None and policy.error_registry is not None:
        registries = (("router policy", policy.error_registry), *registries)
    registry = _merge_errors(
        registries,
        name=composition.errors.registry_name,
        type_base=composition.errors.type_base,
    )
    installed_registry = getattr(app.state, INSTALLED_REGISTRY_STATE_KEY, None)
    if registry is None and isinstance(installed_registry, ErrorRegistry):
        return installed_registry
    return ErrorRegistry.merge(
        *((registry,) if registry is not None else ()),
        name=composition.errors.registry_name,
        type_base=(
            composition.errors.type_base
            or (registry.type_base if registry is not None else None)
            or _DEFAULT_TYPE_BASE
        ),
    )


def _prepare_container(app: FastAPI, composition: Composition) -> AsyncContainer | None:
    _reject_duplicate_contributions(
        composition.features, "providers", "provider source"
    )
    materialized = _materialize_providers(
        _flatten_provider_sources(composition.features)
    )
    _reject_duplicate_materialized_providers(materialized)
    providers = tuple(item.provider for item in materialized)
    instrumentation = tuple(
        (feature.name, spec)
        for feature in composition.features
        for spec in feature.instrumentation
    )
    if instrumentation:
        from fastapi_canon.integrations.opentelemetry.declarations import (
            _instrumentation_providers,
        )

        providers = (
            *providers,
            *_instrumentation_providers(instrumentation, composition.telemetry),
        )
    if composition.overrides:
        materialized_overrides = _materialize_providers(
            tuple(("overrides", source) for source in composition.overrides)
        )
        _reject_duplicate_materialized_providers(
            (*materialized, *materialized_overrides)
        )
        overrides = tuple(item.provider for item in materialized_overrides)
        _reject_unmatched_overrides(providers, overrides)
        providers = (*providers, *overrides)
    container = _make_container(providers, composition.telemetry)
    if container is not None and app.middleware_stack is not None:
        raise FeatureConfigurationError(
            "features with providers must be installed before the app starts"
        )
    return container


def _check_rpc_dependencies(
    app: FastAPI,
    features: tuple[Feature, ...],
    routers: tuple[APIRouter, ...],
    container: AsyncContainer | None,
    issues: list[CheckIssue],
    checks: list[CheckResult],
) -> None:
    attribute = "__canon_rpc_dependency_roots__"
    entries = [
        ("<application>", path, route.endpoint)
        for path, route in iter_contract_routes(app.router.routes, include_mounts=False)
        if hasattr(getattr(route, "endpoint", None), attribute)
    ]
    wrapper = APIRouter(prefix=app.router.prefix)
    for router in routers:
        wrapper.include_router(router)
    owners = [
        feature.name
        for feature in features
        for router in feature.routers
        for _, route in iter_contract_routes(router.routes, include_mounts=False)
        if hasattr(getattr(route, "endpoint", None), attribute)
    ]
    effective = [
        (path, route.endpoint)
        for path, route in iter_contract_routes(wrapper.routes, include_mounts=False)
        if hasattr(getattr(route, "endpoint", None), attribute)
    ]
    entries.extend(
        (owner, path, endpoint)
        for owner, (path, endpoint) in zip(owners, effective, strict=True)
    )
    external_container = (
        container is None and getattr(app.state, "dishka_container", None) is not None
    )
    for owner, path, endpoint in entries:
        try:
            requirements = getattr(endpoint, attribute)()
        except Exception:
            issues.append(
                CheckIssue(
                    "canon.providers.E002",
                    f"RPC dependencies at {path!r} could not be inspected",
                    "Check the mounted channel declarations and handler signatures.",
                    feature_names=(owner,),
                    locations=(f"WEBSOCKET {path}",),
                )
            )
            checks.append(
                CheckResult(f"rpc.method_dependencies:{owner}:{path}", "incomplete")
            )
            continue
        for method, dependencies in requirements:
            name = f"rpc.method_dependencies:{owner}:{path}:{method}"
            if dependencies is None or (dependencies and external_container):
                checks.append(CheckResult(name, "skipped"))
                continue
            missing = [
                dependency
                for dependency in dependencies
                if not _rpc_dependency_available(container, dependency)
            ]
            for dependency in missing:
                qualified = _qualified_name(dependency)
                issues.append(
                    CheckIssue(
                        "canon.providers.E002",
                        f"RPC method {method!r} injects {qualified} without an APP/SESSION/REQUEST binding",
                        "Register the default-component service in a feature provider, or configure an explicit resolver that owns it.",
                        feature_names=(owner,),
                        locations=(f"WEBSOCKET {path} ({method})", qualified),
                    )
                )
            checks.append(CheckResult(name, "failed" if missing else "passed"))


def _rpc_dependency_available(
    container: AsyncContainer | None, dependency: type[Any]
) -> bool:
    if container is None:
        return False
    key = DependencyKey(dependency, "")
    parent: AsyncContainer | None = container
    while parent is not None:
        if parent.registry.get_factory(key) is not None:
            return True
        parent = parent.parent_container
    registry = container.registry.child_registry
    while registry is not None:
        if registry.get_factory(key) is not None:
            return True
        if registry.scope is Scope.REQUEST:
            break
        registry = registry.child_registry
    return False


def _check_operations(
    operations: tuple[OperationInfo, ...],
    issues: list[CheckIssue],
    checks: list[CheckResult],
) -> None:
    seen: dict[str, tuple[OperationInfo, str]] = {}
    failed = False
    for operation, method in (
        (candidate, verb) for candidate in operations for verb in candidate.methods
    ):
        if not operation.include_in_schema or operation.operation_id is None:
            continue
        previous = seen.get(operation.operation_id)
        if previous is not None:
            failed = True
            issues.append(
                CheckIssue(
                    "canon.routing.E002",
                    f"Duplicate operation ID {operation.operation_id!r}",
                    "Set distinct operation_id values on these operations.",
                    feature_names=tuple(
                        dict.fromkeys(
                            (previous[0].feature_name, operation.feature_name)
                        )
                    ),
                    locations=(
                        f"{previous[1]} {previous[0].path}",
                        f"{method} {operation.path}",
                    ),
                )
            )
        seen[operation.operation_id] = (operation, method)
    checks.append(CheckResult("http.operation_ids", "failed" if failed else "passed"))


def _check_http_paths(
    application: APIRouter,
    routers: tuple[APIRouter, ...],
    features: tuple[Feature, ...],
    known: tuple[OperationInfo, ...],
    issues: list[CheckIssue],
    checks: list[CheckResult],
) -> None:
    entries = [
        ("<application>", path, route)
        for path, route in iter_contract_routes(
            application.routes, include_mounts=False
        )
        if isinstance(getattr(route, "original_route", route), Route)
    ]
    if routers:
        wrapper = APIRouter(prefix=application.prefix)
        for router in routers:
            wrapper.include_router(router)
        owners = [
            feature.name
            for feature in features
            for router in feature.routers
            for _, route in iter_contract_routes(router.routes, include_mounts=False)
            if isinstance(getattr(route, "original_route", route), Route)
        ]
        effective = [
            (path, route)
            for path, route in iter_contract_routes(
                wrapper.routes, include_mounts=False
            )
            if isinstance(getattr(route, "original_route", route), Route)
        ]
        entries.extend(
            (owner, path, route)
            for owner, (path, route) in zip(owners, effective, strict=True)
        )
    installed = {
        (op.operation_id, op.path, op.methods): op.feature_name for op in known
    }
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    failed = False
    for owner, path, route in entries:
        methods = tuple(sorted(route.methods or ()))
        identity = getattr(route, "operation_id", None) or getattr(
            route, "unique_id", None
        )
        owner = installed.get((identity, path, methods), owner)
        # Erase parameter names, retaining the exact converter regex and literals.
        pattern = re.sub(r"\(\?P<[^>]+>", "(?:", route.path_regex.pattern)
        for method in methods:
            key = (method, pattern)
            previous = seen.get(key)
            if previous is not None:
                failed = True
                issues.append(
                    CheckIssue(
                        "canon.routing.E003",
                        f"Ambiguous HTTP route {method} {path}",
                        "Use distinct effective paths or HTTP methods; operation IDs do not change dispatch.",
                        feature_names=tuple(dict.fromkeys((previous[0], owner))),
                        locations=(f"{method} {previous[1]}", f"{method} {path}"),
                    )
                )
            else:
                seen[key] = (owner, path)
    checks.append(CheckResult("http.paths", "failed" if failed else "passed"))


def _run_feature_checks(
    features: tuple[Feature, ...],
    description: CompositionInfo,
    issues: list[CheckIssue],
    checks: list[CheckResult],
) -> None:
    for feature, info in zip(features, description.features, strict=True):
        for check in feature.checks:
            name = f"{feature.name}:{_qualified_name(check)}"
            try:
                result: object = check(CheckContext(description, info))
                if iscoroutine(result):
                    result.close()
                    raise TypeError("checks must be synchronous")
                if not isinstance(result, (list, tuple)) or not all(
                    isinstance(issue, CheckIssue) for issue in result
                ):
                    raise TypeError("checks must return CheckIssue objects")
                findings: tuple[CheckIssue, ...] = tuple(result)
            except Exception:
                issues.append(
                    CheckIssue(
                        "canon.checks.E001",
                        f"Feature check {name!r} did not complete",
                        "Fix the check function; it must return a finite sequence of CheckIssue objects.",
                        feature_names=(feature.name,),
                    )
                )
                checks.append(CheckResult(name, "incomplete"))
                continue
            issues.extend(
                replace(issue, feature_names=issue.feature_names or (feature.name,))
                for issue in findings
            )
            checks.append(
                CheckResult(
                    name,
                    "failed"
                    if any(issue.severity == "error" for issue in findings)
                    else "passed",
                )
            )


def _check_installed(
    app: FastAPI, description: CompositionInfo | None, features: tuple[Feature, ...]
) -> CheckReport:
    if description is None:
        raise FeatureConfigurationError("Composition description is unavailable")
    issues: list[CheckIssue] = []
    checks: list[CheckResult] = []
    _check_operations(
        _operation_headers(app.router, description.operations), issues, checks
    )
    _check_http_paths(app.router, (), (), description.operations, issues, checks)
    if any(
        issue.code in {"canon.routing.E002", "canon.routing.E003"} for issue in issues
    ):
        checks.append(CheckResult("http.contracts", "skipped"))
    else:
        cached = app.openapi_schema
        try:
            validate_openapi_contracts(app)
        except ErrorConfigurationError as error:
            issues.append(
                CheckIssue(
                    "canon.contracts.E001",
                    str(error),
                    "Match route response contracts to the composed error catalog.",
                )
            )
            checks.append(CheckResult("http.contracts", "failed"))
        else:
            checks.append(CheckResult("http.contracts", "passed"))
        finally:
            app.openapi_schema = cached
    _run_feature_checks(features, description, issues, checks)
    return CheckReport(tuple(issues), tuple(checks))


def _raise_check_errors(report: CheckReport) -> None:
    if report.has_errors:
        raise FeatureConfigurationError(
            "\n".join(
                f"{issue.code}: {issue.message}\nHint: {issue.hint}"
                for issue in report.issues
                if issue.severity == "error"
            )
        )


def _installed_description(app: FastAPI) -> CompositionInfo:
    installed = getattr(app.state, _INSTALLATION_STATE_KEY, None)
    if not isinstance(installed, _Installation) or installed.description is None:
        raise FeatureConfigurationError(
            "Apply a Composition before inspecting the application"
        )
    return installed.description


def _unique_instances[ItemT](
    values: Sequence[object], expected_type: type[ItemT], parameter: str
) -> tuple[ItemT, ...]:
    result: list[ItemT] = []
    seen: set[int] = set()
    for index, value in enumerate(values):
        if not isinstance(value, expected_type):
            msg = f"{parameter}[{index}] must be a {expected_type.__name__} instance"
            raise FeatureConfigurationError(msg)
        if id(value) in seen:
            msg = f"{parameter}[{index}] duplicates an earlier instance"
            raise FeatureConfigurationError(msg)
        seen.add(id(value))
        result.append(value)
    return tuple(result)


def _validate_applied_composition(app: object, composition: Composition) -> None:
    if not isinstance(app, FastAPI):
        msg = "app must be a FastAPI instance"
        raise FeatureConfigurationError(msg)
    installed = getattr(app.state, _INSTALLATION_STATE_KEY, None)
    expected_router_factory_id = (
        id(composition.router_factory)
        if composition.router_factory is not None
        else None
    )
    if not isinstance(installed, _Installation) or (
        installed.mcp != composition.mcp
        or installed.feature_ids
        != tuple(id(feature) for feature in composition.features)
        or installed.telemetry_id
        != (id(composition.telemetry) if composition.telemetry is not None else None)
        or installed.errors != composition.errors
        or installed.request_logging != composition.request_logging
        or installed.override_ids
        != tuple(id(source) for source in composition.overrides)
        or installed.router_factory_id != expected_router_factory_id
        or installed.router_policy_id
        != (
            id(composition.router_policy)
            if composition.router_policy is not None
            else None
        )
    ):
        msg = "composition must be applied to the application before validation"
        raise FeatureConfigurationError(msg)


def _collect_router_errors(
    routers: tuple[APIRouter, ...], *, feature_name: str
) -> ErrorRegistry | None:
    registries: list[tuple[str, ErrorRegistry]] = []
    seen: set[int] = set()
    for router in routers:
        contributions = list(getattr(router, "_canon_error_registries", ()))
        if isinstance(router, CanonRouter):
            contributions.extend(router.error_registries)
        for _, route in iter_contract_routes(router.routes, include_mounts=False):
            metadata = getattr(route, "openapi_extra", None)
            registry = getattr(metadata, "_error_registry", None)
            if registry is not None:
                contributions.append(registry)
            contributions.extend(getattr(route.endpoint, "_canon_error_registries", ()))
        for registry in contributions:
            if id(registry) in seen:
                continue
            seen.add(id(registry))
            registries.append((feature_name, registry))
    return _merge_errors(
        tuple(registries),
        name=f"{feature_name}-routers" if len(registries) > 1 else None,
        type_base=None,
    )


def _validate_optional_string(value: object, parameter: str) -> None:
    if value is not None and not isinstance(value, str):
        msg = f"{parameter} must be a string or None"
        raise FeatureConfigurationError(msg)


def _validate_feature_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\r" in value
        or "\n" in value
    ):
        msg = "feature name must be a non-empty, single-line string"
        raise FeatureConfigurationError(msg)
    return value


def _validate_unique_feature_names(features: tuple[Feature, ...]) -> None:
    seen: dict[str, int] = {}
    for index, feature in enumerate(features):
        previous = seen.get(feature.name)
        if previous is not None:
            msg = (
                f"feature name {feature.name!r} is duplicated at "
                f"features[{previous}] and features[{index}]"
            )
            raise FeatureConfigurationError(msg)
        seen[feature.name] = index


def _validate_bool(value: object, parameter: str) -> None:
    if not isinstance(value, bool):
        msg = f"{parameter} must be a bool"
        raise FeatureConfigurationError(msg)


def _flatten_routers(features: tuple[Feature, ...]) -> tuple[APIRouter, ...]:
    return tuple(router for feature in features for router in feature.routers)


def _unique_provider_sources(
    values: Sequence[ProviderSource], *, owner: str
) -> tuple[ProviderSource, ...]:
    result: list[ProviderSource] = []
    seen: set[int] = set()
    for index, value in enumerate(values):
        raw_value: object = value
        if not isinstance(raw_value, Provider) and not callable(raw_value):
            msg = (
                f"{owner}[{index}] must be a Provider "
                "instance, Provider class, or zero-argument provider factory"
            )
            raise FeatureConfigurationError(msg)
        if id(value) in seen:
            msg = f"{owner}[{index}] duplicates an earlier provider source"
            raise FeatureConfigurationError(msg)
        seen.add(id(value))
        result.append(value)
    return tuple(result)


def _flatten_provider_sources(
    features: tuple[Feature, ...],
) -> tuple[tuple[str, ProviderSource], ...]:
    return tuple(
        (feature.name, provider)
        for feature in features
        for provider in feature.providers
    )


def _flatten_handlers(
    features: tuple[Feature, ...],
) -> tuple[ExceptionHandlerSpec, ...]:
    return tuple(
        handler for feature in features for handler in feature.exception_handlers
    )


def _reject_duplicate_contributions(
    features: tuple[Feature, ...], attribute: str, kind: str
) -> None:
    seen: dict[int, str] = {}
    for feature in features:
        values = getattr(feature, attribute)
        for value in values:
            previous = seen.get(id(value))
            if previous is not None:
                msg = (
                    f"features {previous!r} and {feature.name!r} contribute the "
                    f"same {kind} instance"
                )
                raise FeatureConfigurationError(msg)
            seen[id(value)] = feature.name


def _materialize_providers(
    sources: tuple[tuple[str, ProviderSource], ...],
) -> tuple[_MaterializedProvider, ...]:
    result: list[_MaterializedProvider] = []
    for feature_name, source in sources:
        if isinstance(source, Provider):
            provider = source
        else:
            try:
                candidate: object = source()
            except Exception as error:
                msg = f"provider factory from feature {feature_name!r} failed"
                raise FeatureConfigurationError(msg) from error
            if not isinstance(candidate, Provider):
                msg = (
                    f"provider factory from feature {feature_name!r} returned "
                    f"{type(candidate).__name__}, expected Provider"
                )
                raise FeatureConfigurationError(msg)
            provider = candidate
        result.append(_MaterializedProvider(feature_name, provider))
    return tuple(result)


def _reject_duplicate_materialized_providers(
    providers: tuple[_MaterializedProvider, ...],
) -> None:
    seen: dict[int, str] = {}
    for contribution in providers:
        previous = seen.get(id(contribution.provider))
        if previous is not None:
            msg = (
                f"provider sources from features {previous!r} and "
                f"{contribution.feature_name!r} produced the same Provider instance"
            )
            raise FeatureConfigurationError(msg)
        seen[id(contribution.provider)] = contribution.feature_name


def _reject_unmatched_overrides(
    providers: tuple[Provider, ...], overrides: tuple[Provider, ...]
) -> None:
    provided = {key for provider in providers for key in _provided_keys(provider)}
    for override in overrides:
        for key in _provided_keys(override):
            if key not in provided:
                msg = (
                    f"override {type(override).__name__} provides "
                    f"{_qualified_name(key.type_hint)}, which no feature provides"
                )
                raise FeatureConfigurationError(msg)


def _provided_keys(provider: Provider) -> tuple[DependencyKey, ...]:
    keys = (
        *(factory.provides for factory in provider.factories),
        *(alias.provides for alias in provider.aliases),
        *(variable.provides for variable in provider.context_vars),
    )
    return tuple(key.with_component(provider.component) for key in keys)


def _prepare_routers(
    routers: tuple[APIRouter, ...],
    router_factory: RouterFactory | None,
    router_policy: RouterPolicy | None,
    validate_paths: Callable[[str], None],
) -> tuple[APIRouter, ...]:
    prepared = _prepare_application_router(routers, router_factory, validate_paths)
    if router_policy is None or not router_policy.dependencies:
        return prepared
    wrapper = APIRouter(dependencies=list(router_policy.dependencies))
    for router in prepared:
        wrapper.include_router(router)
    return (wrapper,)


def _prepare_application_router(
    routers: tuple[APIRouter, ...],
    router_factory: RouterFactory | None,
    validate_paths: Callable[[str], None],
) -> tuple[APIRouter, ...]:
    if router_factory is None:
        validate_paths("")
        return routers
    try:
        wrapper: object = router_factory()
    except Exception as error:
        msg = "router_factory failed"
        raise FeatureConfigurationError(msg) from error
    if not isinstance(wrapper, APIRouter):
        msg = f"router_factory returned {type(wrapper).__name__}, expected APIRouter"
        raise FeatureConfigurationError(msg)
    if wrapper.routes:
        msg = "router_factory must return a fresh APIRouter without routes"
        raise FeatureConfigurationError(msg)
    validate_paths(wrapper.prefix)
    try:
        for router in routers:
            wrapper.include_router(router)
    except Exception as error:
        msg = "composition router failed to include feature routers"
        raise FeatureConfigurationError(msg) from error
    return (wrapper,)


def _validate_handlers(app: FastAPI, features: tuple[Feature, ...]) -> None:
    seen: dict[type[Exception], str] = {}
    for feature in features:
        for spec in feature.exception_handlers:
            previous = seen.get(spec.exception)
            if previous is not None:
                msg = (
                    f"features {previous!r} and {feature.name!r} define an "
                    "exception handler for "
                    f"{spec.exception.__qualname__}"
                )
                raise FeatureConfigurationError(msg)
            seen[spec.exception] = feature.name
            if spec.exception in app.exception_handlers:
                msg = (
                    f"feature {feature.name!r} defines an exception handler for "
                    f"{spec.exception.__qualname__}, but the application already "
                    "defines one"
                )
                raise FeatureConfigurationError(msg)


def _merge_errors(
    registries: tuple[tuple[str, ErrorRegistry], ...],
    *,
    name: str | None,
    type_base: str | None,
) -> ErrorRegistry | None:
    if not registries:
        return None
    distinct: list[tuple[str, ErrorRegistry]] = []
    seen: set[int] = set()
    for contribution in registries:
        if id(contribution[1]) in seen:
            continue
        seen.add(id(contribution[1]))
        distinct.append(contribution)
    registries = tuple(distinct)
    inferred_base = type_base
    registry_bases = {registry.type_base for _, registry in registries}
    if inferred_base is None and len(registry_bases) == 1:
        inferred_base = next(iter(registry_bases))
    try:
        return ErrorRegistry.merge(
            *(registry for _, registry in registries),
            name=name,
            type_base=inferred_base,
        )
    except ErrorConfigurationError as error:
        feature_names = ", ".join(repr(feature_name) for feature_name, _ in registries)
        msg = (
            f"error registries from features {feature_names} are incompatible: {error}"
        )
        raise FeatureConfigurationError(msg) from error


def _make_container(
    providers: tuple[Provider, ...], telemetry: Telemetry | None
) -> AsyncContainer | None:
    if not providers:
        return None
    from fastapi_canon.integrations.opentelemetry import Telemetry

    for provider in providers:
        if (
            any(
                factory.provides.type_hint is Telemetry
                for factory in provider.factories
            )
            or any(alias.provides.type_hint is Telemetry for alias in provider.aliases)
            or any(
                variable.provides.type_hint is Telemetry
                for variable in provider.context_vars
            )
        ):
            raise FeatureConfigurationError(
                "Telemetry is reserved by Composition; remove the custom binding"
            )
    options = telemetry if telemetry is not None else Telemetry.disabled()
    components = dict.fromkeys(provider.component for provider in providers)
    providers = (
        *providers,
        *(options.provider(component=component) for component in components),
    )
    try:
        return make_async_container(*providers)
    except DishkaError as error:
        msg = f"invalid Dishka provider graph: {error}"
        raise FeatureConfigurationError(msg) from error


def _validate_error_installation(
    app: FastAPI,
    routers: tuple[APIRouter, ...],
    handler_specs: tuple[ExceptionHandlerSpec, ...],
    registry: ErrorRegistry | None,
    *,
    include_validation_error: bool,
    include_http_exceptions: bool,
    include_unhandled_error: bool,
    router_policy: RouterPolicy | None,
) -> frozenset[str]:
    if app.openapi_schema is not None:
        msg = "install features before generating or caching OpenAPI"
        raise FeatureConfigurationError(msg)
    validation_app = FastAPI(
        dependencies=list(app.router.dependencies),
        generate_unique_id_function=app.router.generate_unique_id_function,
    )
    validation_app.router.routes = list(app.router.routes)
    installed_registry = getattr(app.state, INSTALLED_REGISTRY_STATE_KEY, None)
    if isinstance(installed_registry, ErrorRegistry) and installed_registry is registry:
        try:
            registry.install(
                app,
                include_validation_error=include_validation_error,
                include_http_exceptions=include_http_exceptions,
                include_unhandled_error=include_unhandled_error,
            )
            registry.install(
                validation_app,
                include_validation_error=include_validation_error,
                include_http_exceptions=include_http_exceptions,
                include_unhandled_error=include_unhandled_error,
            )
        except ErrorConfigurationError as error:
            raise FeatureConfigurationError(str(error)) from error
    validation_app.exception_handlers.update(app.exception_handlers)
    for router in routers:
        validation_app.include_router(router)
    for spec in handler_specs:
        validation_app.add_exception_handler(spec.exception, spec.handler)
    operations: frozenset[str] = frozenset()
    if router_policy is not None and router_policy.raises:
        policy_router = APIRouter()
        for router in routers:
            policy_router.include_router(router)
        operations = frozenset(
            f"{method} {operation.path}"
            for operation in _operation_headers(policy_router)
            if operation.include_in_schema
            for method in operation.methods
        )
    try:
        if registry is not None:
            registry.install(
                validation_app,
                include_validation_error=include_validation_error,
                include_http_exceptions=include_http_exceptions,
                include_unhandled_error=include_unhandled_error,
            )
        else:
            install_openapi_contracts(validation_app)
        if router_policy is not None and router_policy.raises:
            install_policy_contracts(validation_app, router_policy.raises, operations)
        validation_app.openapi()
    except ErrorConfigurationError as error:
        raise FeatureConfigurationError(str(error)) from error
    return operations


def _router_lifespans(
    original: FeatureLifespan, routers: tuple[APIRouter, ...]
) -> tuple[FeatureLifespan, ...]:
    """Return router lifespans that the composed app lifespan must run.

    A router without ``lifespan=`` gets a default lifespan that runs its
    ``on_startup``/``on_shutdown`` handlers. ``include_router`` also copies those
    handlers to the app, whose own default lifespan runs them. Keep the router's
    default lifespan only when the app's lifespan does not, so each handler runs
    exactly once.
    """
    default = type(APIRouter().lifespan_context)
    app_runs_handlers = isinstance(original, default)
    return tuple(
        router.lifespan_context
        for router in routers
        if not (app_runs_handlers and isinstance(router.lifespan_context, default))
    )


def _compose_lifespan(
    original: FeatureLifespan,
    feature_lifespans: tuple[FeatureLifespan, ...],
    container: AsyncContainer | None,
) -> FeatureLifespan:
    @asynccontextmanager
    async def composed(app: FastAPI) -> AsyncIterator[Mapping[str, Any]]:
        async with AsyncExitStack() as stack:
            if container is not None:
                stack.push_async_callback(container.close)
            validate_openapi_contracts(app)
            states: list[Mapping[str, object]] = []
            original_state = await stack.enter_async_context(original(app))
            if original_state is not None:
                states.append(original_state)
            for lifespan in feature_lifespans:
                state = await stack.enter_async_context(lifespan(app))
                if state is not None:
                    states.append(state)
            merged_state = {
                key: value for state in states for key, value in state.items()
            }
            yield merged_state

    return composed
