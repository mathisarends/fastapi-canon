from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from fastapi_canon.error.contracts import SUCCESS_EXTENSION
from fastapi_canon.error.error import Error
from fastapi_canon.error.registry import AnyError, ErrorRegistry
from fastapi_canon.error.types import ErrorConfigurationError, OpenAPIResponses
from fastapi_canon.error.validation import unique_instances
from fastapi_canon.response import CanonResponse, ResponseConfigurationError


class CanonRouter(APIRouter):
    """An APIRouter that composes shared and operation response contracts."""

    def __init__(
        self,
        *,
        error_registry: ErrorRegistry | None = None,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        **kwargs: Any,
    ) -> None:
        raw_error_registry: object = error_registry
        if raw_error_registry is not None and not isinstance(
            raw_error_registry, ErrorRegistry
        ):
            msg = "error_registry must be an ErrorRegistry instance or None"
            raise ErrorConfigurationError(msg)
        self.error_registry = error_registry
        self._included_error_registries: list[ErrorRegistry] = []
        self.raises = _normalize_raises(
            raises,
            registry=error_registry,
            parameter="raises",
        )
        self.http_statuses = _normalize_http_statuses(http_statuses)
        super().__init__(**kwargs)

    @property
    def error_registries(self) -> tuple[ErrorRegistry, ...]:
        """Registries owned by this router and its included Canon routers."""
        registries = (
            () if self.error_registry is None else (self.error_registry,)
        ) + tuple(self._included_error_registries)
        return tuple({id(registry): registry for registry in registries}.values())

    def include_router(self, router: APIRouter, **kwargs: Any) -> None:
        """Include routes and preserve ownership of their error definitions."""
        super().include_router(router, **kwargs)
        if isinstance(router, CanonRouter):
            self._included_error_registries.extend(router.error_registries)

    def api_route[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Any,
    ) -> Callable[[CallableT], CallableT]:
        """Declare a route and compile its inherited and local contracts."""
        local_raises = _normalize_raises(
            raises,
            registry=self.error_registry,
            parameter="raises",
        )
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
        return super().api_route(path, **kwargs)

    def get[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Any,
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route("GET", path, raises, http_statuses, response, kwargs)

    def put[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Any,
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route("PUT", path, raises, http_statuses, response, kwargs)

    def post[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Any,
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route("POST", path, raises, http_statuses, response, kwargs)

    def delete[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Any,
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
        **kwargs: Any,
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
        **kwargs: Any,
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route("HEAD", path, raises, http_statuses, response, kwargs)

    def patch[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Any,
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
        **kwargs: Any,
    ) -> Callable[[CallableT], CallableT]:
        return self._method_route(
            "TRACE", path, raises, http_statuses, response, kwargs
        )

    def _method_route[CallableT: Callable[..., Any]](
        self,
        method: str,
        path: str,
        raises: Sequence[AnyError],
        http_statuses: Sequence[int],
        response: CanonResponse | None,
        kwargs: dict[str, Any],
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


def _normalize_raises(
    values: Sequence[AnyError],
    *,
    registry: ErrorRegistry | None,
    parameter: str,
) -> tuple[AnyError, ...]:
    raw_values: object = values
    if isinstance(raw_values, str) or not isinstance(raw_values, Sequence):
        msg = f"{parameter} must be a sequence of Error instances"
        raise ErrorConfigurationError(msg)
    normalized = tuple(unique_instances(values, Error, parameter=parameter))
    if normalized and registry is None:
        msg = f"{parameter} requires an error registry"
        raise ErrorConfigurationError(msg)
    if registry is not None:
        for error in normalized:
            if not registry.contains(error):
                msg = f"error {error.code!r} does not belong to this router's registry"
                raise ErrorConfigurationError(msg)
    return normalized


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
