from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from typing import Any, TypedDict, Unpack

from fastapi import APIRouter
from fastapi.params import Depends
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.types import IncEx
from starlette.routing import BaseRoute, get_name
from starlette.types import ASGIApp, Lifespan

from fastapi_canon.error import Error, ErrorConfigurationError, ErrorRegistry
from fastapi_canon.error.contracts import SUCCESS_EXTENSION, AnyError
from fastapi_canon.error.openapi import InheritedContract
from fastapi_canon.error.types import OpenAPIResponses
from fastapi_canon.error.validation import unique_instances
from fastapi_canon.exceptions import ConfigurationError, ResponseConfigurationError
from fastapi_canon.policy import RouterPolicy, normalize_raises, validate_policy
from fastapi_canon.response import CanonResponse


class OpenAPIOptions(TypedDict, total=False):
    """Metadata accepted by both HTTP declarations and router inclusion."""

    tags: list[str | Enum] | None
    dependencies: Sequence[Depends] | None
    responses: dict[int | str, dict[str, Any]] | None
    callbacks: list[BaseRoute] | None
    deprecated: bool | None
    include_in_schema: bool
    generate_unique_id_function: Callable[[APIRoute], str]


class IncludeRouterOptions(OpenAPIOptions, total=False):
    """FastAPI options accepted by ``include_router()``."""

    prefix: str
    default_response_class: type[Response]


class FastAPIRouterOptions(IncludeRouterOptions, total=False):
    """FastAPI constructor options preserved by Canon routers."""

    routes: list[BaseRoute] | None
    redirect_slashes: bool
    default: ASGIApp | None
    dependency_overrides_provider: Any
    route_class: type[APIRoute]
    on_startup: Sequence[Callable[[], Any]] | None
    on_shutdown: Sequence[Callable[[], Any]] | None
    lifespan: Lifespan[Any] | None
    strict_content_type: bool


class RouterOptions(FastAPIRouterOptions, total=False):
    """Constructor options accepted by the public ``APIRouter``."""

    namespace: str | None
    raises: Sequence[AnyError]
    http_statuses: Sequence[int]
    policy: RouterPolicy | None


class RouteOptions(OpenAPIOptions, total=False):
    """FastAPI options accepted by HTTP method decorators."""

    response_model: Any
    status_code: int | None
    summary: str | None
    description: str | None
    response_description: str
    operation_id: str | None
    response_model_include: IncEx | None
    response_model_exclude: IncEx | None
    response_model_by_alias: bool
    response_model_exclude_unset: bool
    response_model_exclude_defaults: bool
    response_model_exclude_none: bool
    response_class: type[Response]
    name: str | None
    openapi_extra: dict[str, Any] | None


class APIRouteOptions(RouteOptions, total=False):
    """FastAPI options accepted by the general ``api_route()`` decorator."""

    methods: list[str] | None


class CanonRouter(APIRouter):
    """Compose response contracts and name operations from their endpoint functions.

    Like router dependencies, the router's ``raises``, ``http_statuses`` and
    ``policy`` apply to its own operations and to every HTTP operation it
    includes with ``include_router``.
    """

    def __init__(
        self,
        *,
        namespace: str | None = None,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        policy: RouterPolicy | None = None,
        **kwargs: Unpack[FastAPIRouterOptions],
    ) -> None:
        if namespace is not None and (
            not isinstance(namespace, str)
            or any(
                not part.isidentifier() or part.startswith("_")
                for part in namespace.split(".")
            )
        ):
            raise ConfigurationError(
                "namespace must be a dotted public identifier or None"
            )
        self.namespace = namespace
        validate_policy(policy)
        self.policy = policy
        # Collected from raises; Composition merges it into the application catalog.
        self.error_registry: ErrorRegistry | None = None
        self._included_error_registries: list[ErrorRegistry] = []
        self.raises = normalize_raises(raises, parameter="raises")
        self.http_statuses = _normalize_http_statuses(http_statuses)
        if policy is not None:
            self.raises = _distinct((*policy.raises, *self.raises))
            kwargs["dependencies"] = [
                *policy.dependencies,
                *(kwargs.get("dependencies") or ()),
            ]
        self._register_errors(self.raises)
        generate_id = kwargs.pop(
            "generate_unique_id_function", self._generate_operation_id
        )
        super().__init__(**kwargs)
        # Specialized transport constructors need not accept HTTP naming options.
        self.generate_unique_id_function = generate_id
        if self.raises or self.http_statuses:
            # FastAPI applies router callbacks to every operation the router adds
            # or includes, which carries this contract to child routes.
            self.callbacks = [
                *(self.callbacks or ()),
                InheritedContract(self.raises, self.http_statuses),
            ]

    @property
    def error_registries(self) -> tuple[ErrorRegistry, ...]:
        """Registries owned by this router and its included Canon routers."""
        registries = (
            (() if self.error_registry is None else (self.error_registry,))
            + tuple(self._included_error_registries)
            + tuple(getattr(self, "_canon_error_registries", ()))
        )
        return tuple({id(registry): registry for registry in registries}.values())

    def include_router(
        self, router: APIRouter, **kwargs: Unpack[IncludeRouterOptions]
    ) -> None:
        """Include routes and preserve ownership of their error definitions."""
        super().include_router(router, **kwargs)
        if isinstance(router, CanonRouter):
            self._included_error_registries.extend(router.error_registries)
        else:
            self._included_error_registries.extend(
                getattr(router, "_canon_error_registries", ())
            )

    def add_api_websocket_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        name: str | None = None,
        *,
        dependencies: Sequence[Depends] | None = None,
    ) -> None:
        registries = (
            *getattr(endpoint, "_canon_error_registries", ()),
            *self.error_registries,
        )
        endpoint.__dict__["_canon_error_registries"] = tuple(dict.fromkeys(registries))
        super().add_api_websocket_route(
            path, endpoint, name=name, dependencies=dependencies
        )

    def api_route[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Unpack[APIRouteOptions],
    ) -> Callable[[CallableT], CallableT]:
        """Declare a route and compile its inherited and local contracts."""
        local_raises = normalize_raises(raises, parameter="raises")
        self._register_errors(local_raises)
        raw_response: object = response
        if raw_response is not None and not isinstance(raw_response, CanonResponse):
            msg = "response must be a fastapi_canon.CanonResponse instance or None"
            raise ResponseConfigurationError(msg)

        status_code = kwargs.get("status_code")
        if response is not None:
            if response.media_type is None and kwargs.get("response_model") is not None:
                msg = f"route {path!r} response_model conflicts with bodyless CanonResponse"
                raise ResponseConfigurationError(msg)
            if status_code is None:
                kwargs["status_code"] = response.status
            elif status_code != response.status:
                msg = (
                    f"route {path!r} declares response status {response.status}, but "
                    f"status_code is {status_code}"
                )
                raise ResponseConfigurationError(msg)
            kwargs.setdefault("response_class", _response_class(response))

        configured_responses = kwargs.pop("responses", None)
        kwargs["responses"] = self._responses_for_route(
            local_raises,
            http_statuses=_normalize_http_statuses(http_statuses),
            response=response,
            configured=configured_responses,
        )
        dependency_origins = {str(id(dep)): "router" for dep in self.dependencies}
        dependency_origins.update(
            {str(id(dep)): "operation" for dep in (kwargs.get("dependencies") or ())}
        )
        error_origins = {str(id(error)): "operation" for error in local_raises}
        error_origins.update({str(id(error)): "router" for error in self.raises})
        if self.policy is not None:
            dependency_origins.update(
                {str(id(dep)): "router.policy" for dep in self.policy.dependencies}
            )
            error_origins.update(
                {str(id(error)): "router.policy" for error in self.policy.raises}
            )

        def register(endpoint: CallableT) -> CallableT:
            configured: APIRouteOptions = {
                **kwargs,
                "openapi_extra": _RouteMetadata(
                    kwargs.get("openapi_extra") or {},
                    dependency_origins,
                    error_origins,
                    endpoint,
                    self.error_registry,
                ),
            }
            return super(CanonRouter, self).api_route(path, **configured)(endpoint)

        return register

    def get[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Unpack[RouteOptions],
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route("GET", path, raises, http_statuses, response, kwargs)

    def put[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Unpack[RouteOptions],
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route("PUT", path, raises, http_statuses, response, kwargs)

    def post[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Unpack[RouteOptions],
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route("POST", path, raises, http_statuses, response, kwargs)

    def delete[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Unpack[RouteOptions],
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route(
            "DELETE", path, raises, http_statuses, response, kwargs
        )

    def options[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Unpack[RouteOptions],
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route(
            "OPTIONS", path, raises, http_statuses, response, kwargs
        )

    def head[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Unpack[RouteOptions],
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route("HEAD", path, raises, http_statuses, response, kwargs)

    def patch[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Unpack[RouteOptions],
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route(
            "PATCH", path, raises, http_statuses, response, kwargs
        )

    def trace[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Unpack[RouteOptions],
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route(
            "TRACE", path, raises, http_statuses, response, kwargs
        )

    def _generate_operation_id(self, route: APIRoute) -> str:
        name = get_name(route.endpoint)
        return f"{self.namespace}.{name}" if self.namespace is not None else name

    def _register_errors(self, errors: Sequence[AnyError]) -> None:
        registry = self.error_registry
        if not errors or (
            registry is not None and all(registry.contains(error) for error in errors)
        ):
            return
        self.error_registry = ErrorRegistry.merge(
            *((registry,) if registry is not None else ()),
            ErrorRegistry(errors=errors),
            type_base=registry.type_base if registry is not None else None,
        )

    def _method_route[CallableT: Callable[..., Any]](
        self,
        method: str,
        path: str,
        raises: Sequence[AnyError],
        http_statuses: Sequence[int],
        response: CanonResponse | None,
        kwargs: RouteOptions,
    ) -> Callable[[CallableT], CallableT]:
        return self.api_route(
            path,
            methods=[method],
            raises=raises,
            http_statuses=http_statuses,
            response=response,
            **kwargs,
        )

    def _responses_for_route(
        self,
        local_raises: tuple[AnyError, ...],
        *,
        http_statuses: tuple[int, ...],
        response: CanonResponse | None,
        configured: object,
    ) -> OpenAPIResponses:
        manual = _normalize_responses(configured)
        declared = _distinct((*self.raises, *local_raises))
        statuses = tuple(dict.fromkeys((*self.http_statuses, *http_statuses)))
        if declared or statuses:
            registry = self.error_registry
            if registry is None:
                registry = ErrorRegistry(errors=[])
            canon = registry.responses(
                *declared, http_statuses=statuses, success=response
            )
            if response is not None:
                canon[response.status].pop(SUCCESS_EXTENSION, None)
        elif response is not None:
            canon = {response.status: response.as_openapi()}
        else:
            return manual

        inherited_statuses = {_status_key(status) for status in self.responses}
        manual_statuses = {_status_key(status) for status in manual}
        for status in canon:
            normalized = _status_key(status)
            if normalized in inherited_statuses or normalized in manual_statuses:
                msg = (
                    f"Canon response status {status} conflicts with a FastAPI "
                    "responses declaration"
                )
                raise ResponseConfigurationError(msg)
        return {**manual, **canon}


class _RouteMetadata(dict[str, Any]):
    """Retain declaration provenance across FastAPI route copies without JSON keys."""

    def __init__(
        self,
        values: Mapping[str, Any],
        dependencies: dict[str, str],
        errors: dict[str, str],
        endpoint: Callable[..., object],
        registry: ErrorRegistry | None,
    ) -> None:
        super().__init__(values)
        self._dependency_origins = tuple(
            (int(key), value) for key, value in dependencies.items()
        )
        self._error_origins = tuple(errors.items())
        self._endpoint = getattr(endpoint, "__canon_declared_endpoint__", endpoint)
        self._error_registry = registry


def _normalize_responses(value: object) -> OpenAPIResponses:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        msg = "responses must be a mapping or None"
        raise ResponseConfigurationError(msg)
    return dict(value)


def _distinct(errors: Sequence[AnyError]) -> tuple[AnyError, ...]:
    return tuple(unique_instances(errors, Error, parameter="raises"))


def _status_key(value: object) -> str:
    return str(value)


def _response_class(response: CanonResponse) -> type[Response]:
    media_type = response.media_type
    if media_type == "application/json" or (
        media_type is not None and media_type.endswith("+json")
    ):
        return JSONResponse
    return Response


def _normalize_http_statuses(values: Sequence[int]) -> tuple[int, ...]:
    raw_values: object = values
    if isinstance(raw_values, str) or not isinstance(raw_values, Sequence):
        raise ErrorConfigurationError(
            "http_statuses must be a sequence of HTTP statuses"
        )
    for index, status in enumerate(values):
        if (
            not isinstance(status, int)
            or isinstance(status, bool)
            or not 400 <= status <= 599
        ):
            raise ErrorConfigurationError(
                f"http_statuses[{index}] must be between 400 and 599"
            )
    return tuple(dict.fromkeys(values))
