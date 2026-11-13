from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from dishka import (
    AsyncContainer,
)
from fastapi import APIRouter, FastAPI
from starlette.types import Lifespan

from fastapi_canon.composition_checks import (
    flatten_handlers,
    prepare_composition,
    raise_check_errors,
)
from fastapi_canon.contract_routes import iter_contract_mounts, iter_contract_routes
from fastapi_canon.diagnostics import (
    CompositionInfo,
)
from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.openapi import (
    validate_openapi_contracts,
)
from fastapi_canon.request_logging import install_request_logging
from fastapi_canon.scopes import install_scopes

if TYPE_CHECKING:
    from fastapi_canon.feature import Composition, ErrorOptions, Feature
    from fastapi_canon.integrations.mcp import McpOptions
    from fastapi_canon.integrations.opentelemetry import Telemetry

INSTALLATION_STATE_KEY = "_fastapi_canon_installation"
type FeatureLifespan = Lifespan[FastAPI]


def installed_contract_routes(
    app: FastAPI,
) -> tuple[tuple[Feature, ...], tuple[tuple[Feature, str, object], ...]]:
    """Return installed features and the routes each registered, across mounts.

    Routes are ``(feature, effective path, original route)`` triples. Mounted
    FastAPI applications contribute their own compositions under the mount path.
    """
    installed = getattr(app.state, INSTALLATION_STATE_KEY, None)
    features = installed.features if isinstance(installed, Installation) else ()
    routes = installed.contract_routes if isinstance(installed, Installation) else ()
    for prefix, child in iter_contract_mounts(app.routes):
        if isinstance(child, FastAPI):
            child_features, child_routes = installed_contract_routes(child)
            features = (*features, *child_features)
            routes = (
                *routes,
                *(
                    (feature, prefix + path, route)
                    for feature, path, route in child_routes
                ),
            )
    return features, routes


@dataclass(frozen=True, slots=True)
class Installation:
    features: tuple[Feature, ...] = field(compare=False)
    feature_ids: tuple[int, ...]
    errors: ErrorOptions
    router_factory: Callable[[], APIRouter] | None = field(compare=False)
    router_factory_id: int | None
    telemetry: Telemetry | None = field(compare=False)
    telemetry_id: int | None
    request_logging: bool
    mcp: McpOptions | None
    override_ids: tuple[int, ...]
    container: AsyncContainer | None = field(compare=False)
    description: CompositionInfo | None = field(default=None, compare=False)
    contract_routes: tuple[tuple[Feature, str, object], ...] = field(
        default=(), compare=False
    )


def apply_composition(app: FastAPI, composition: Composition) -> bool:
    raw_app: object = app
    if not isinstance(raw_app, FastAPI):
        msg = "app must be a FastAPI instance"
        raise FeatureConfigurationError(msg)
    requested = Installation(
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
    )
    installed = getattr(app.state, INSTALLATION_STATE_KEY, None)
    if installed is not None:
        if not isinstance(installed, Installation) or installed != requested:
            msg = "a different composition is already applied to this application"
            raise FeatureConfigurationError(msg)
        return False

    prepared = prepare_composition(app, composition)
    if prepared.missing_dependency is not None:
        raise prepared.missing_dependency
    raise_check_errors(prepared.report)
    if prepared.description is None:
        raise FeatureConfigurationError("Composition description is unavailable")
    telemetry = composition.telemetry
    mounts = tuple(mount for feature in composition.features for mount in feature.mcp)
    handlers = flatten_handlers(composition.features)
    lifespans = tuple(
        feature.lifespan
        for feature in composition.features
        if feature.lifespan is not None
    )
    installed_routers = prepared.routers
    registry = prepared.registry
    if registry is None:
        raise FeatureConfigurationError("Composed error catalog is unavailable")
    container = prepared.container

    if telemetry is not None:
        from fastapi_canon.integrations.opentelemetry.native import install

        install(app, telemetry)
    original_lifespan = app.router.lifespan_context
    if container is not None:
        install_scopes(app, container)
    previous_routes = len(tuple(iter_contract_routes(app.routes)))
    for router in installed_routers:
        app.include_router(router)
    owners = tuple(
        feature
        for feature in composition.features
        for router in feature.routers
        for _ in iter_contract_routes(router.routes)
    )
    registered = tuple(iter_contract_routes(app.routes))[previous_routes:]
    contract_routes = tuple(
        (feature, path, getattr(route, "original_route", route))
        for feature, (path, route) in zip(owners, registered, strict=True)
    )
    for exception, handler in handlers:
        app.add_exception_handler(exception, handler)
    registry.install(
        app,
        include_validation_error=composition.errors.include_validation_error,
        include_http_exceptions=composition.errors.include_http_exceptions,
        include_unhandled_error=composition.errors.include_unhandled_error,
    )
    if composition.mcp is not None:
        from fastapi_canon.integrations.mcp.runtime import install_mcp

        if prepared.mcp_runtime is None:
            raise FeatureConfigurationError("MCP runtime configuration is unavailable")
        lifespans = (
            *lifespans,
            install_mcp(app, mounts, composition.mcp, container, prepared.mcp_runtime),
        )
    app.router.lifespan_context = compose_lifespan(
        original_lifespan,
        # include_router merged these into the app context after we captured
        # original_lifespan. Retain them inside the dependency lifetime.
        (*lifespans, *router_lifespans(original_lifespan, installed_routers)),
        container,
    )

    if composition.request_logging:
        install_request_logging(app)

    completed = replace(
        requested,
        container=container,
        description=prepared.description,
        contract_routes=contract_routes,
    )
    setattr(app.state, INSTALLATION_STATE_KEY, completed)
    return True


def installed_description(app: FastAPI) -> CompositionInfo:
    installed = getattr(app.state, INSTALLATION_STATE_KEY, None)
    if not isinstance(installed, Installation) or installed.description is None:
        raise FeatureConfigurationError(
            "Apply a Composition before inspecting the application"
        )
    return installed.description


def validate_applied_composition(app: object, composition: Composition) -> None:
    if not isinstance(app, FastAPI):
        msg = "app must be a FastAPI instance"
        raise FeatureConfigurationError(msg)
    installed = getattr(app.state, INSTALLATION_STATE_KEY, None)
    expected_router_factory_id = (
        id(composition.router_factory)
        if composition.router_factory is not None
        else None
    )
    if not isinstance(installed, Installation) or (
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
    ):
        msg = "composition must be applied to the application before validation"
        raise FeatureConfigurationError(msg)


def router_lifespans(
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


def compose_lifespan(
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
