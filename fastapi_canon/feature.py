from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING

from dishka import (
    Provider,
)
from fastapi import APIRouter, FastAPI
from starlette.types import ExceptionHandler, Lifespan

from fastapi_canon.composition_checks import (
    check_installed,
    collect_router_errors,
    merge_errors,
    prepare_composition,
    raise_check_errors,
)
from fastapi_canon.composition_installation import (
    INSTALLATION_STATE_KEY,
    Installation,
    apply_composition,
    installed_description,
    validate_applied_composition,
)
from fastapi_canon.composition_installation import (
    installed_contract_routes as installed_contract_routes,
)
from fastapi_canon.diagnostics import (
    CheckIssue,
    CheckReport,
    CheckResult,
    CompositionInfo,
    FeatureCheck,
)
from fastapi_canon.error import ErrorRegistry
from fastapi_canon.error.contracts import AnyError
from fastapi_canon.exceptions import ErrorConfigurationError, FeatureConfigurationError
from fastapi_canon.openapi import (
    validate_openapi_contracts,
)
from fastapi_canon.policy import normalize_raises

if TYPE_CHECKING:
    from fastapi_canon.integrations.mcp import McpMount, McpOptions
    from fastapi_canon.integrations.opentelemetry import Instrumentation, Telemetry


type FeatureLifespan = Lifespan[FastAPI]


@dataclass(frozen=True, slots=True, init=False)
class Feature:
    """An immutable declaration of one feature's FastAPI contributions."""

    name: str
    routers: tuple[APIRouter, ...]
    providers: tuple[Provider | Callable[[], Provider], ...]
    instrumentation: tuple[Instrumentation, ...]
    mcp: tuple[McpMount, ...]
    exception_handlers: Mapping[type[Exception], ExceptionHandler]
    lifespan: FeatureLifespan | None
    checks: tuple[FeatureCheck, ...]
    _listed_errors: tuple[AnyError, ...] = field(repr=False)

    def __init__(
        self,
        *,
        name: str,
        routers: Sequence[APIRouter] = (),
        providers: Sequence[Provider | Callable[[], Provider]] = (),
        instrumentation: Sequence[Instrumentation] = (),
        mcp: Sequence[McpMount] = (),
        errors: Sequence[AnyError] = (),
        exception_handlers: Mapping[type[Exception], ExceptionHandler] | None = None,
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

            normalized_mcp = unique_instances(normalized_mcp, McpMount, "mcp")
        object.__setattr__(self, "mcp", normalized_mcp)
        normalized_name = _validate_feature_name(name)
        normalized_routers = unique_instances(
            routers, APIRouter, f"feature {normalized_name!r} routers"
        )
        normalized_providers = unique_provider_sources(
            providers, owner=f"feature {normalized_name!r} providers"
        )
        normalized_instrumentation = tuple(instrumentation)
        if normalized_instrumentation:
            from fastapi_canon.integrations.opentelemetry import Instrumentation

            normalized_instrumentation = unique_instances(
                normalized_instrumentation,
                Instrumentation,
                f"feature {normalized_name!r} instrumentation",
            )
        normalized_handlers = _normalize_handlers(
            exception_handlers, feature_name=normalized_name
        )
        raw_lifespan: object = lifespan
        try:
            listed = normalize_raises(
                errors, parameter=f"feature {normalized_name!r} errors"
            )
        except ErrorConfigurationError as error:
            raise FeatureConfigurationError(str(error)) from error
        if raw_lifespan is not None and not callable(raw_lifespan):
            msg = f"feature {normalized_name!r} lifespan must be callable or None"
            raise FeatureConfigurationError(msg)

        router_errors = collect_router_errors(
            normalized_routers, feature_name=normalized_name
        )
        merge_errors(
            tuple(
                (normalized_name, registry)
                for registry in (
                    ErrorRegistry(errors=listed) if listed else None,
                    router_errors,
                )
                if registry is not None
            ),
            name=normalized_name,
            type_base=None,
        )

        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "routers", normalized_routers)
        object.__setattr__(self, "providers", normalized_providers)
        object.__setattr__(self, "instrumentation", normalized_instrumentation)
        object.__setattr__(self, "_listed_errors", listed)
        object.__setattr__(self, "exception_handlers", normalized_handlers)
        object.__setattr__(self, "lifespan", lifespan)

    @property
    def errors(self) -> tuple[AnyError, ...]:
        """Explicit contributions plus errors declared on the current routers."""
        registry = self.error_registry
        return registry.errors if registry is not None else ()

    @property
    def error_registry(self) -> ErrorRegistry | None:
        router_errors = collect_router_errors(self.routers, feature_name=self.name)
        return merge_errors(
            tuple(
                (self.name, registry)
                for registry in (
                    ErrorRegistry(errors=self._listed_errors)
                    if self._listed_errors
                    else None,
                    router_errors,
                )
                if registry is not None
            ),
            name=self.name,
            type_base=None,
        )


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
    router_factory: Callable[[], APIRouter] | None
    telemetry: Telemetry | None
    request_logging: bool
    mcp: McpOptions | None
    overrides: tuple[Provider | Callable[[], Provider], ...]

    def __init__(
        self,
        *,
        features: Sequence[Feature] = (),
        errors: ErrorOptions | None = None,
        router_factory: Callable[[], APIRouter] | None = None,
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
        normalized_features = unique_instances(features, Feature, "features")
        validate_unique_feature_names(normalized_features)
        raw_errors: object = errors
        raw_router_factory: object = router_factory
        if raw_errors is not None and not isinstance(raw_errors, ErrorOptions):
            msg = "errors must be an ErrorOptions instance or None"
            raise FeatureConfigurationError(msg)
        if raw_router_factory is not None and not callable(raw_router_factory):
            msg = "router_factory must be callable or None"
            raise FeatureConfigurationError(msg)
        object.__setattr__(self, "features", normalized_features)
        object.__setattr__(self, "errors", errors or ErrorOptions())
        object.__setattr__(self, "router_factory", router_factory)
        object.__setattr__(self, "overrides", ())

    def build(self, app: FastAPI) -> FastAPI:
        """Install this composition into *app*, validate it and return the app.

        Building the same composition again only repeats validation.
        """
        if not apply_composition(app, self):
            raise_check_errors(self.check(app))
        # Regenerate OpenAPI so routes added since an earlier build are included.
        app.openapi_schema = None
        validate_openapi_contracts(app)
        return app

    def override(self, *providers: Provider | Callable[[], Provider]) -> Composition:
        """Return a copy whose *providers* replace existing Dishka bindings.

        Overrides are added after every feature provider, so their bindings win.
        Each override may only provide types that a feature already provides.
        """
        if not providers:
            raise FeatureConfigurationError("override expects at least one provider")
        overrides = unique_provider_sources(
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
        installed = getattr(app.state, INSTALLATION_STATE_KEY, None)
        if installed is not None:
            try:
                validate_applied_composition(app, self)
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
            assert isinstance(installed, Installation)
            return check_installed(app, installed.description, self.features)
        return prepare_composition(app, self).report


def inspect_app(app: FastAPI) -> CompositionInfo:
    """Return the HTTP snapshot recorded when a Composition was built into *app*.

    The snapshot is detached: routes added after the build are not included.
    """
    raw_app: object = app
    if not isinstance(raw_app, FastAPI):
        raise FeatureConfigurationError("app must be a FastAPI instance")
    return installed_description(app)


def unique_instances[ItemT](
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


def validate_unique_feature_names(features: tuple[Feature, ...]) -> None:
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


def unique_provider_sources(
    values: Sequence[Provider | Callable[[], Provider]], *, owner: str
) -> tuple[Provider | Callable[[], Provider], ...]:
    result: list[Provider | Callable[[], Provider]] = []
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


def _validate_bool(value: object, parameter: str) -> None:
    if not isinstance(value, bool):
        msg = f"{parameter} must be a bool"
        raise FeatureConfigurationError(msg)


def _normalize_handlers(
    handlers: Mapping[type[Exception], ExceptionHandler] | None, *, feature_name: str
) -> Mapping[type[Exception], ExceptionHandler]:
    raw_handlers: object = handlers
    if raw_handlers is None:
        return _HandlerMap({})
    if not isinstance(raw_handlers, Mapping):
        msg = (
            f"feature {feature_name!r} exception_handlers must be a mapping of "
            "exception classes to handlers"
        )
        raise FeatureConfigurationError(msg)
    normalized: dict[type[Exception], ExceptionHandler] = {}
    for exception, handler in raw_handlers.items():
        if not isinstance(exception, type) or not issubclass(exception, Exception):
            msg = (
                f"feature {feature_name!r} exception_handlers keys must be "
                "Exception subclasses"
            )
            raise FeatureConfigurationError(msg)
        if not callable(handler):
            msg = (
                f"feature {feature_name!r} exception handler for "
                f"{exception.__qualname__} must be callable"
            )
            raise FeatureConfigurationError(msg)
        normalized[exception] = handler
    return _HandlerMap(normalized)


class _HandlerMap(Mapping[type[Exception], ExceptionHandler]):
    # Read-only and hashable, so Feature keeps its frozen dataclass hash.
    __slots__ = ("_handlers",)

    def __init__(self, handlers: dict[type[Exception], ExceptionHandler]) -> None:
        self._handlers = dict(handlers)

    def __getitem__(self, key: type[Exception]) -> ExceptionHandler:
        return self._handlers[key]

    def __iter__(self) -> Iterator[type[Exception]]:
        return iter(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)

    def __hash__(self) -> int:
        return hash(tuple(self._handlers.items()))

    def __repr__(self) -> str:
        return repr(self._handlers)
