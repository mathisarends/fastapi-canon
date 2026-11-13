from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from inspect import iscoroutine
from typing import TYPE_CHECKING

from dishka import (
    DEFAULT_COMPONENT,
    AsyncContainer,
    Provider,
    Scope,
    make_async_container,
)
from dishka.entities.key import DependencyKey
from dishka.exception_base import DishkaError
from dishka.integrations.base import is_dishka_injected
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from starlette.routing import Match, Route, WebSocketRoute, compile_path
from starlette.types import ExceptionHandler
from starlette.types import Scope as ASGIScope

from fastapi_canon.contract_routes import iter_contract_routes
from fastapi_canon.diagnostics import (
    CheckContext,
    CheckIssue,
    CheckReport,
    CheckResult,
    CompositionInfo,
    OperationInfo,
)
from fastapi_canon.error import ErrorRegistry
from fastapi_canon.error.contracts import INSTALLED_REGISTRY_STATE_KEY
from fastapi_canon.error.contributions import error_registries
from fastapi_canon.exceptions import ErrorConfigurationError, FeatureConfigurationError
from fastapi_canon.injected_route import InjectedRoute, endpoint_dependencies
from fastapi_canon.injection import injected_resolver_dependency
from fastapi_canon.introspection import (
    describe_composition,
    http_routes,
    operation_headers,
    qualified_name,
)
from fastapi_canon.openapi import (
    install_openapi_contracts,
    validate_openapi_contracts,
)
from fastapi_canon.router import CanonRouter

if TYPE_CHECKING:
    from fastapi_canon.feature import Composition, Feature
    from fastapi_canon.integrations.mcp.configuration import McpRuntime
    from fastapi_canon.integrations.opentelemetry import Telemetry

_DEFAULT_TYPE_BASE = "urn:fastapi-canon:problem"


@dataclass(frozen=True, slots=True)
class Preparation:
    report: CheckReport
    routers: tuple[APIRouter, ...]
    registry: ErrorRegistry | None
    container: AsyncContainer | None
    description: CompositionInfo | None
    missing_dependency: ModuleNotFoundError | None
    mcp_runtime: McpRuntime | None


@dataclass(frozen=True, slots=True)
class MaterializedProvider:
    feature_name: str
    provider: Provider


class WebSocketPathError(FeatureConfigurationError):
    def __init__(self, issue: CheckIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def prepare_composition(app: FastAPI, composition: Composition) -> Preparation:
    return _CompositionChecks(app, composition).run()


def validate_runtime_options(app: FastAPI, composition: Composition) -> None:
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


def prepare_feature_routers(
    app: FastAPI, composition: Composition
) -> tuple[APIRouter, ...]:
    reject_duplicate_contributions(composition.features, "routers", "router")
    return prepare_routers(
        flatten_routers(composition.features),
        composition.router_factory,
        lambda prefix: validate_websocket_paths(app, composition.features, prefix),
    )


def validate_websocket_paths(
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
                raise WebSocketPathError(
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


def validate_mcp_routes(
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


def prepare_registry(
    app: FastAPI, composition: Composition, routers: tuple[APIRouter, ...] | None
) -> ErrorRegistry:
    registries = tuple(
        (feature.name, feature_registry)
        for feature in composition.features
        if (feature_registry := feature.error_registry) is not None
    )
    if composition.router_factory is not None and routers:
        # The application router's own raises and policy extend every feature.
        root = routers[0]
        if isinstance(root, CanonRouter) and root.error_registry is not None:
            registries = (("router_factory", root.error_registry), *registries)
    registry = merge_errors(
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


def prepare_container(app: FastAPI, composition: Composition) -> AsyncContainer | None:
    reject_duplicate_contributions(composition.features, "providers", "provider source")
    materialized = materialize_providers(flatten_provider_sources(composition.features))
    reject_duplicate_materialized_providers(materialized)
    providers = tuple(item.provider for item in materialized)
    instrumentation = tuple(
        (feature.name, spec)
        for feature in composition.features
        for spec in feature.instrumentation
    )
    if instrumentation:
        from fastapi_canon.integrations.opentelemetry.declarations import (
            instrumentation_providers,
        )

        providers = (
            *providers,
            *instrumentation_providers(instrumentation, composition.telemetry),
        )
    if composition.overrides:
        materialized_overrides = materialize_providers(
            tuple(("overrides", source) for source in composition.overrides)
        )
        reject_duplicate_materialized_providers(
            (*materialized, *materialized_overrides)
        )
        overrides = tuple(item.provider for item in materialized_overrides)
        reject_unmatched_overrides(providers, overrides)
        providers = (*providers, *overrides)
    container = make_container(providers, composition.telemetry)
    if container is not None and app.middleware_stack is not None:
        raise FeatureConfigurationError(
            "features with providers must be installed before the app starts"
        )
    return container


def check_rpc_dependencies(
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
                if not dependency_available(
                    container, DependencyKey(dependency, ""), Scope.REQUEST
                )
            ]
            for dependency in missing:
                qualified = qualified_name(dependency)
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


def check_webrtc_dependencies(
    app: FastAPI,
    features: tuple[Feature, ...],
    routers: tuple[APIRouter, ...],
    container: AsyncContainer | None,
    issues: list[CheckIssue],
    checks: list[CheckResult],
) -> None:
    attribute = "__canon_webrtc_dependency_roots__"
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
    external = (
        container is None and getattr(app.state, "dishka_container", None) is not None
    )
    for owner, path, endpoint in entries:
        roots = getattr(endpoint, attribute)
        name = f"webrtc.factory_dependencies:{owner}:{path}"
        if roots is None or (roots and external):
            checks.append(CheckResult(name, "skipped"))
            continue
        missing = [
            key
            for key in roots
            if not dependency_available(container, key, Scope.SESSION)
        ]
        for key in missing:
            qualified = qualified_name(key.type_hint)
            issues.append(
                CheckIssue(
                    "canon.providers.E004",
                    f"WebRTC factory {path!r} injects {qualified} without an APP/SESSION binding (component {key.component!r})",
                    "Register the factory service at APP or SESSION scope; use a child REQUEST scope for per-message work.",
                    feature_names=(owner,),
                    locations=(f"WEBSOCKET {path}", qualified),
                )
            )
        checks.append(CheckResult(name, "failed" if missing else "passed"))


def dependency_available(
    container: AsyncContainer | None, key: DependencyKey, through_scope: Scope
) -> bool:
    if container is None:
        return False
    parent: AsyncContainer | None = container
    while parent is not None:
        if parent.registry.get_factory(key) is not None:
            return True
        parent = parent.parent_container
    if container.scope is through_scope:
        return False
    registry = container.registry.child_registry
    while registry is not None:
        if registry.get_factory(key) is not None:
            return True
        if registry.scope is through_scope:
            break
        registry = registry.child_registry
    return False


def check_http_dependencies(
    app: FastAPI,
    features: tuple[Feature, ...],
    description: CompositionInfo,
    container: AsyncContainer | None,
    issues: list[CheckIssue],
    checks: list[CheckResult],
) -> None:
    entries = [
        ("<application>", route.path, route) for route in http_routes(app.router)
    ]
    sources = [
        (feature.name, route)
        for feature in features
        for router in feature.routers
        for route in http_routes(router)
    ]
    entries.extend(
        (owner, operation.path, route)
        for (owner, route), operation in zip(
            sources, description.operations, strict=True
        )
    )
    external = (
        container is None and getattr(app.state, "dishka_container", None) is not None
    )
    for owner, path, route in entries:
        original = route.original_route
        name = f"http.endpoint_dependencies:{owner}:{'|'.join(route.methods)}:{path}"
        if type(original) not in (InjectedRoute, DishkaRoute, APIRoute):
            checks.append(CheckResult(name, "skipped"))
            continue
        if type(original) is APIRoute and not (
            is_dishka_injected(original.endpoint)
            or hasattr(original.endpoint, "__canon_http_dependency_roots__")
        ):
            continue
        endpoint = getattr(route.openapi_extra, "_endpoint", original.endpoint)
        try:
            dependencies = endpoint_dependencies(endpoint)
        except Exception:
            issues.append(
                CheckIssue(
                    "canon.providers.E003",
                    f"HTTP endpoint dependencies at {path!r} could not be inspected",
                    "Check endpoint injection annotations and wrapper metadata.",
                    feature_names=(owner,),
                    locations=(f"{'|'.join(route.methods)} {path}",),
                )
            )
            checks.append(CheckResult(name, "incomplete"))
            continue
        nested = nested_injections(route.dependant)
        if (dependencies or nested) and external:
            checks.append(CheckResult(name, "skipped"))
            continue
        stream = bool(route.openapi_extra and "x-canon-stream" in route.openapi_extra)
        scope = Scope.APP if stream else Scope.REQUEST
        # FastAPI dependencies run before a stream starts, inside the request scope.
        missing = [
            key
            for key in dependencies
            if not dependency_available(container, key, scope)
        ] + [
            key
            for key in nested
            if key not in dependencies
            and not dependency_available(container, key, Scope.REQUEST)
        ]
        for key in missing:
            qualified = qualified_name(key.type_hint)
            issues.append(
                CheckIssue(
                    "canon.providers.E003",
                    f"{'SSE' if stream else 'HTTP'} endpoint {path!r} injects {qualified} without a binding available through {scope.name} scope (component {key.component!r})",
                    "Use APP services or an APP-scoped ServiceScope factory for stream work; HTTP REQUEST resources close before streaming."
                    if stream
                    else "Register this endpoint service and component in a feature provider at APP/SESSION/REQUEST scope.",
                    feature_names=(owner,),
                    locations=(f"{'|'.join(route.methods)} {path}", qualified),
                )
            )
        checks.append(CheckResult(name, "failed" if missing else "passed"))


def nested_injections(dependant: Dependant | None) -> tuple[DependencyKey, ...]:
    pending = list(dependant.dependencies) if dependant is not None else []
    keys: list[DependencyKey] = []
    while pending:
        current = pending.pop(0)
        service = injected_resolver_dependency(current.call)
        if service is not None:
            keys.append(DependencyKey(service, DEFAULT_COMPONENT))
        pending.extend(current.dependencies)
    return tuple(dict.fromkeys(keys))


def check_operations(
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


def check_http_paths(
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
        path_regex = route.path_regex or compile_path(route.path)[0]
        pattern = re.sub(r"\(\?P<[^>]+>", "(?:", path_regex.pattern)
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


def run_feature_checks(
    features: tuple[Feature, ...],
    description: CompositionInfo,
    issues: list[CheckIssue],
    checks: list[CheckResult],
) -> None:
    for feature, info in zip(features, description.features, strict=True):
        for check in feature.checks:
            name = f"{feature.name}:{qualified_name(check)}"
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


def check_installed(
    app: FastAPI, description: CompositionInfo | None, features: tuple[Feature, ...]
) -> CheckReport:
    if description is None:
        raise FeatureConfigurationError("Composition description is unavailable")
    issues: list[CheckIssue] = []
    checks: list[CheckResult] = []
    check_operations(
        operation_headers(app.router, description.operations), issues, checks
    )
    check_http_paths(app.router, (), (), description.operations, issues, checks)
    if any(
        check.name in {"http.operation_ids", "http.paths"} and check.status == "failed"
        for check in checks
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
    run_feature_checks(features, description, issues, checks)
    return CheckReport(tuple(issues), tuple(checks))


def raise_check_errors(report: CheckReport) -> None:
    if report.has_errors:
        raise FeatureConfigurationError(
            "\n".join(
                f"{issue.code}: {issue.message}\nHint: {issue.hint}"
                for issue in report.issues
                if issue.severity == "error"
            )
        )


def collect_router_errors(
    routers: tuple[APIRouter, ...], *, feature_name: str
) -> ErrorRegistry | None:
    registries: list[tuple[str, ErrorRegistry]] = []
    seen: set[int] = set()
    for router in routers:
        contributions = list(error_registries(router))
        for _, route in iter_contract_routes(router.routes, include_mounts=False):
            metadata = getattr(route, "openapi_extra", None)
            registry = getattr(metadata, "_error_registry", None)
            if registry is not None:
                contributions.append(registry)
            contributions.extend(error_registries(route.endpoint))
        for registry in contributions:
            if id(registry) in seen:
                continue
            seen.add(id(registry))
            registries.append((feature_name, registry))
    return merge_errors(
        tuple(registries),
        name=f"{feature_name}-routers" if len(registries) > 1 else None,
        type_base=None,
    )


def flatten_routers(features: tuple[Feature, ...]) -> tuple[APIRouter, ...]:
    return tuple(router for feature in features for router in feature.routers)


def flatten_provider_sources(
    features: tuple[Feature, ...],
) -> tuple[tuple[str, Provider | Callable[[], Provider]], ...]:
    return tuple(
        (feature.name, provider)
        for feature in features
        for provider in feature.providers
    )


def flatten_handlers(
    features: tuple[Feature, ...],
) -> tuple[tuple[type[Exception], ExceptionHandler], ...]:
    return tuple(
        pair for feature in features for pair in feature.exception_handlers.items()
    )


def reject_duplicate_contributions(
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


def materialize_providers(
    sources: tuple[tuple[str, Provider | Callable[[], Provider]], ...],
) -> tuple[MaterializedProvider, ...]:
    result: list[MaterializedProvider] = []
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
        result.append(MaterializedProvider(feature_name, provider))
    return tuple(result)


def reject_duplicate_materialized_providers(
    providers: tuple[MaterializedProvider, ...],
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


def reject_unmatched_overrides(
    providers: tuple[Provider, ...], overrides: tuple[Provider, ...]
) -> None:
    provided = {key for provider in providers for key in provided_keys(provider)}
    for override in overrides:
        for key in provided_keys(override):
            if key not in provided:
                msg = (
                    f"override {type(override).__name__} provides "
                    f"{qualified_name(key.type_hint)}, which no feature provides"
                )
                raise FeatureConfigurationError(msg)


def provided_keys(provider: Provider) -> tuple[DependencyKey, ...]:
    keys = (
        *(factory.provides for factory in provider.factories),
        *(alias.provides for alias in provider.aliases),
        *(variable.provides for variable in provider.context_vars),
    )
    return tuple(key.with_component(provider.component) for key in keys)


def prepare_routers(
    routers: tuple[APIRouter, ...],
    router_factory: Callable[[], APIRouter] | None,
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
        msg = f"composition router failed to include feature routers: {error}"
        raise FeatureConfigurationError(msg) from error
    return (wrapper,)


def validate_handlers(app: FastAPI, features: tuple[Feature, ...]) -> None:
    seen: dict[type[Exception], str] = {}
    for feature in features:
        for exception in feature.exception_handlers:
            previous = seen.get(exception)
            if previous is not None:
                msg = (
                    f"features {previous!r} and {feature.name!r} define an "
                    "exception handler for "
                    f"{exception.__qualname__}"
                )
                raise FeatureConfigurationError(msg)
            seen[exception] = feature.name
            if exception in app.exception_handlers:
                msg = (
                    f"feature {feature.name!r} defines an exception handler for "
                    f"{exception.__qualname__}, but the application already "
                    "defines one"
                )
                raise FeatureConfigurationError(msg)


def merge_errors(
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


def make_container(
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


def validate_error_installation(
    app: FastAPI,
    routers: tuple[APIRouter, ...],
    handlers: tuple[tuple[type[Exception], ExceptionHandler], ...],
    registry: ErrorRegistry | None,
    *,
    include_validation_error: bool,
    include_http_exceptions: bool,
    include_unhandled_error: bool,
) -> None:
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
    for exception, handler in handlers:
        validation_app.add_exception_handler(exception, handler)
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
        validation_app.openapi()
    except ErrorConfigurationError as error:
        raise FeatureConfigurationError(str(error)) from error


@dataclass(frozen=True, slots=True)
class _CheckStep:
    name: str
    action: Callable[[], None]
    prerequisites: tuple[str, ...] = ()
    code: str = ""
    hint: str = ""
    enabled: Callable[[], bool] = lambda: True


class _CompositionChecks:
    def __init__(self, app: FastAPI, composition: Composition) -> None:
        self.app = app
        self.composition = composition
        self.issues: list[CheckIssue] = []
        self.checks: list[CheckResult] = []
        self.missing_dependencies: list[ModuleNotFoundError] = []
        self.routers: tuple[APIRouter, ...] | None = None
        self.registry: ErrorRegistry | None = None
        self.container: AsyncContainer | None = None
        self.description: CompositionInfo | None = None
        self.mcp_runtime: McpRuntime | None = None

    def run(self) -> Preparation:
        steps = (
            _CheckStep(
                "installation",
                self._runtime_options,
                code="canon.composition.E001",
                hint="Install before startup and OpenAPI generation; check integration options.",
            ),
            _CheckStep(
                "http.routers",
                self._prepare_routers,
                code="canon.routing.E001",
                hint="Contribute each router once and return a fresh empty application router.",
            ),
            _CheckStep(
                "mcp.routes",
                self._mcp_routes,
                ("http.routers",),
                code="canon.mcp.E001",
                hint="Move the conflicting HTTP route or choose a different MCP path.",
                enabled=lambda: self.composition.mcp is not None,
            ),
            _CheckStep(
                "handlers",
                self._handlers,
                code="canon.handlers.E001",
                hint="Keep one handler per exception across the application and its features.",
            ),
            _CheckStep(
                "errors",
                self._prepare_errors,
                code="canon.errors.E001",
                hint="Use compatible error definitions and one explicit application type base.",
            ),
            _CheckStep(
                "providers",
                self._providers,
                code="canon.providers.E001",
                hint="Check the named provider sources and supply the missing Dishka bindings.",
            ),
            _CheckStep(
                "rpc.method_dependencies",
                self._rpc_dependencies,
                ("http.routers", "providers"),
            ),
            _CheckStep(
                "webrtc.factory_dependencies",
                self._webrtc_dependencies,
                ("http.routers", "providers"),
            ),
            _CheckStep(
                "mcp.runtime",
                self._prepare_mcp,
                code="canon.mcp.E002",
                hint="Use synchronous factories that borrow the APP container without starting resources.",
                enabled=lambda: (
                    self.composition.mcp is not None and not self.missing_dependencies
                ),
            ),
            _CheckStep(
                "http.description",
                self._describe,
                ("http.routers", "errors", "handlers"),
                code="canon.description.E001",
                hint="Ensure declared HTTP errors are present in the composed catalog.",
            ),
            _CheckStep(
                "http.operation_ids", self._operation_ids, ("http.description",)
            ),
            _CheckStep("http.paths", self._http_paths, ("http.description",)),
            _CheckStep(
                "http.endpoint_dependencies",
                self._http_dependencies,
                ("http.description", "providers"),
            ),
            _CheckStep(
                "http.contracts",
                self._contracts,
                ("http.description", "http.operation_ids", "http.paths"),
                code="canon.contracts.E001",
                hint="Match route response contracts to the composed error catalog.",
            ),
        )
        passed: set[str] = set()
        for step in steps:
            if not step.enabled():
                continue
            if not all(name in passed for name in step.prerequisites):
                self.checks.append(CheckResult(step.name, "skipped"))
                continue
            before = len(self.checks)
            try:
                step.action()
            except (ErrorConfigurationError, ModuleNotFoundError) as error:
                self._record_failure(step, error)
            else:
                results = self.checks[before:]
                if not results:
                    self.checks.append(CheckResult(step.name, "passed"))
                if all(result.status == "passed" for result in results):
                    passed.add(step.name)
        if self.description is not None:
            run_feature_checks(
                self.composition.features, self.description, self.issues, self.checks
            )
        elif any(feature.checks for feature in self.composition.features):
            self.checks.append(CheckResult("feature.checks", "skipped"))
        return Preparation(
            CheckReport(tuple(self.issues), tuple(self.checks)),
            self.routers or (),
            self.registry,
            self.container,
            self.description,
            self.missing_dependencies[0] if self.missing_dependencies else None,
            self.mcp_runtime,
        )

    def _record_failure(
        self, step: _CheckStep, error: ErrorConfigurationError | ModuleNotFoundError
    ) -> None:
        if isinstance(error, ModuleNotFoundError):
            self.missing_dependencies.append(error)
        participants = {
            "providers": tuple(
                f.name
                for f in self.composition.features
                if f.providers or f.instrumentation
            ),
            "handlers": tuple(
                f.name for f in self.composition.features if f.exception_handlers
            ),
            "http.routers": tuple(
                f.name for f in self.composition.features if f.routers
            ),
            "errors": tuple(f.name for f in self.composition.features if f.errors),
        }
        issue = (
            error.issue
            if isinstance(error, WebSocketPathError)
            else CheckIssue(
                step.code,
                str(error),
                step.hint,
                feature_names=participants.get(
                    step.name, tuple(f.name for f in self.composition.features)
                ),
                locations=tuple(
                    qualified_name(p)
                    for f in self.composition.features
                    for p in f.providers
                )
                if step.name == "providers"
                else (),
            )
        )
        self.issues.append(issue)
        self.checks.append(CheckResult(step.name, "failed"))

    def _runtime_options(self) -> None:
        validate_runtime_options(self.app, self.composition)

    def _prepare_routers(self) -> None:
        self.routers = prepare_feature_routers(self.app, self.composition)

    def _mcp_routes(self) -> None:
        assert self.composition.mcp is not None and self.routers is not None
        validate_mcp_routes(self.app, self.routers, self.composition.mcp.path)

    def _handlers(self) -> None:
        validate_handlers(self.app, self.composition.features)

    def _prepare_errors(self) -> None:
        self.registry = prepare_registry(self.app, self.composition, self.routers)

    def _providers(self) -> None:
        self.container = prepare_container(self.app, self.composition)

    def _rpc_dependencies(self) -> None:
        assert self.routers is not None
        check_rpc_dependencies(
            self.app,
            self.composition.features,
            self.routers,
            self.container,
            self.issues,
            self.checks,
        )

    def _webrtc_dependencies(self) -> None:
        assert self.routers is not None
        check_webrtc_dependencies(
            self.app,
            self.composition.features,
            self.routers,
            self.container,
            self.issues,
            self.checks,
        )

    def _prepare_mcp(self) -> None:
        from fastapi_canon.integrations.mcp.configuration import prepare_runtime

        assert self.composition.mcp is not None
        self.mcp_runtime = prepare_runtime(self.composition.mcp, self.container)

    def _describe(self) -> None:
        assert self.routers is not None
        self.description = describe_composition(
            tuple(
                (f.name, f.routers, tuple(qualified_name(p) for p in f.providers))
                for f in self.composition.features
            ),
            self.routers,
            self.registry,
            self.app.router,
        )

    def _operation_ids(self) -> None:
        assert self.description is not None
        check_operations(
            (*operation_headers(self.app.router), *self.description.operations),
            self.issues,
            self.checks,
        )

    def _http_paths(self) -> None:
        assert self.routers is not None
        check_http_paths(
            self.app.router,
            self.routers,
            self.composition.features,
            (),
            self.issues,
            self.checks,
        )

    def _http_dependencies(self) -> None:
        assert self.description is not None
        check_http_dependencies(
            self.app,
            self.composition.features,
            self.description,
            self.container,
            self.issues,
            self.checks,
        )

    def _contracts(self) -> None:
        assert self.routers is not None
        options = self.composition.errors
        validate_error_installation(
            self.app,
            self.routers,
            flatten_handlers(self.composition.features),
            self.registry,
            include_validation_error=options.include_validation_error,
            include_http_exceptions=options.include_http_exceptions,
            include_unhandled_error=options.include_unhandled_error,
        )
