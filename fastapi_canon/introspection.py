"""Adapt effective FastAPI HTTP routes into detached composition metadata."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from fastapi.dependencies.models import Dependant
from fastapi.params import Depends
from fastapi.routing import APIRoute

from fastapi_canon.contract_routes import iter_contract_routes
from fastapi_canon.diagnostics import (
    CompositionInfo,
    DependencyInfo,
    ErrorInfo,
    FeatureInfo,
    OperationInfo,
)
from fastapi_canon.error.contracts import (
    ErrorCatalog,
    contracts_from_responses,
)
from fastapi_canon.error.types import FeatureConfigurationError
from fastapi_canon.policy import RouterPolicy
from fastapi_canon.router import CanonRouter


@dataclass(frozen=True, slots=True)
class _HttpRoute:
    path: str
    operation_id: str | None
    unique_id: str
    methods: tuple[str, ...]
    include_in_schema: bool
    responses: Mapping[str, Any]
    dependencies: tuple[Depends, ...]
    dependant: Dependant | None
    openapi_extra: Mapping[str, Any] | None
    original_route: APIRoute


def _describe(
    features: Sequence[tuple[str, tuple[APIRouter, ...], tuple[str, ...]]],
    routers: tuple[APIRouter, ...],
    catalog: ErrorCatalog | None,
    policy: RouterPolicy | None,
    application_router: APIRouter,
) -> CompositionInfo:
    sources = [
        (name, router, route)
        for name, contributions, _ in features
        for router in contributions
        for route in _http_routes(router)
    ]
    wrapper = APIRouter(
        prefix=application_router.prefix,
        dependencies=list(application_router.dependencies),
        generate_unique_id_function=application_router.generate_unique_id_function,
        include_in_schema=application_router.include_in_schema,
    )
    for router in routers:
        wrapper.include_router(router)
    effective = list(_http_routes(wrapper))
    if len(sources) != len(effective):
        raise FeatureConfigurationError(
            "Prepared routers changed the number of HTTP operations"
        )
    operations: list[OperationInfo] = []
    for (name, router, source), route in zip(sources, effective, strict=True):
        dependencies = _dependencies(
            route, source, router, policy, application_router.dependencies
        )
        errors: list[ErrorInfo] = []
        shared = policy.raises if policy is not None else ()
        declared, _, _ = contracts_from_responses(route.path, route.responses, catalog)
        error_origins = dict(getattr(source.openapi_extra, "_error_origins", ()))
        for error in dict.fromkeys((*shared, *declared)):
            origin = error_origins.get(str(id(error)), "route")
            if error in shared:
                origin = "composition.router_policy"
            elif origin == "route" and isinstance(router, CanonRouter):
                if router.policy is not None and error in router.policy.raises:
                    origin = "router.policy"
                elif error in router.raises:
                    origin = "router"
            errors.append(
                ErrorInfo(
                    error.code,
                    catalog.type_uri_for(error) if catalog is not None else error.type,
                    error.status,
                    origin,
                    error.title,
                )
            )
        operations.append(
            OperationInfo(
                feature_name=name,
                operation_id=route.operation_id or route.unique_id,
                path=route.path,
                methods=tuple(sorted(route.methods)),
                dependencies=dependencies,
                errors=tuple(errors),
                include_in_schema=route.include_in_schema,
            )
        )
    return CompositionInfo(
        features=tuple(
            FeatureInfo(
                name,
                tuple(op for op in operations if op.feature_name == name),
                providers,
            )
            for name, _, providers in features
        ),
        operations=tuple(operations),
        endpoints=tuple(route.original_route.endpoint for route in effective),
    )


def _http_routes(router: APIRouter) -> Iterator[_HttpRoute]:
    for path, route in iter_contract_routes(router.routes, include_mounts=False):
        original = getattr(route, "original_route", route)
        if isinstance(original, APIRoute):
            yield _HttpRoute(
                path,
                route.operation_id,
                route.unique_id,
                tuple(sorted(route.methods)),
                route.include_in_schema,
                route.responses,
                tuple(route.dependencies),
                route.dependant,
                route.openapi_extra,
                original,
            )


def _operation_headers(
    router: APIRouter, known: tuple[OperationInfo, ...] = ()
) -> tuple[OperationInfo, ...]:
    owners = {(op.operation_id, op.path, op.methods): op.feature_name for op in known}
    result = []
    for route in _http_routes(router):
        identity = route.operation_id or route.unique_id
        methods = tuple(sorted(route.methods))
        result.append(
            OperationInfo(
                owners.get((identity, route.path, methods), "<application>"),
                identity,
                route.path,
                methods,
                include_in_schema=route.include_in_schema,
            )
        )
    return tuple(result)


def _dependencies(
    route: _HttpRoute,
    source: _HttpRoute,
    router: APIRouter,
    policy: RouterPolicy | None,
    application_dependencies: Sequence[Depends],
) -> tuple[DependencyInfo, ...]:
    original = getattr(source, "original_route", source)
    local = {id(dependency) for dependency in original.dependencies}
    declared = dict(getattr(source.openapi_extra, "_dependency_origins", ()))
    origins = {
        id(dependency): "operation"
        if not declared and id(dependency) in local
        else "router.include_router"
        for dependency in source.dependencies
    }
    for dependency in router.dependencies:
        origins[id(dependency)] = "router"
    if isinstance(router, CanonRouter) and router.policy is not None:
        for dependency in router.policy.dependencies:
            origins[id(dependency)] = "router.policy"
    origins.update(declared)
    if policy is not None:
        for dependency in policy.dependencies:
            origins[id(dependency)] = "composition.router_policy"
    for dependency in application_dependencies:
        origins[id(dependency)] = "application"
    result = [
        DependencyInfo(
            _qualified_name(dep.dependency),
            origins.get(id(dep), "composition.router_factory"),
        )
        for dep in route.dependencies
        if isinstance(dep, Depends) and dep.dependency is not None
    ]
    # Include dependencies expressed in endpoint parameters, including nested
    # Depends/Security declarations, without resolving or invoking any of them.
    pending = list(route.dependant.dependencies) if route.dependant is not None else []
    while pending:
        parameter_dependency = pending.pop(0)
        name = _qualified_name(parameter_dependency.call)
        if not any(item.name == name for item in result):
            result.append(DependencyInfo(name, "dependency"))
        pending.extend(parameter_dependency.dependencies)
    return tuple(result)


def _qualified_name(value: object) -> str:
    subject = value if hasattr(value, "__qualname__") else type(value)
    return (
        f"{getattr(subject, '__module__', '')}.{getattr(subject, '__qualname__', '')}"
    )
