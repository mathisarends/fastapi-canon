from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import ExceptionHandler
from starlette.websockets import WebSocket, WebSocketState

from fastapi_canon.error.contracts import (
    INSTALLED_HANDLERS_STATE_KEY,
    INSTALLED_REGISTRY_STATE_KEY,
)
from fastapi_canon.error.openapi import install_openapi
from fastapi_canon.error.rendering import render_problem
from fastapi_canon.error.types import (
    ErrorConfigurationError,
    JsonValue,
    http_problem_title,
)

if TYPE_CHECKING:
    from fastapi_canon.error.registry import ErrorRegistry

logger = logging.getLogger(__name__)


def install_handlers(
    registry: ErrorRegistry,
    app: FastAPI,
    *,
    include_validation_error: bool,
    include_http_exceptions: bool,
    include_unhandled_error: bool,
) -> None:
    """Install handlers once while preserving user-owned handler conflicts."""
    if not isinstance(app, FastAPI):
        raise ErrorConfigurationError("app must be a FastAPI instance")
    options = (
        include_validation_error,
        include_http_exceptions,
        include_unhandled_error,
    )
    for name, value in zip(
        (
            "include_validation_error",
            "include_http_exceptions",
            "include_unhandled_error",
        ),
        options,
        strict=True,
    ):
        if not isinstance(value, bool):
            raise ErrorConfigurationError(f"{name} must be a bool")
    installed = getattr(app.state, INSTALLED_REGISTRY_STATE_KEY, None)
    if installed is registry:
        if getattr(app.state, INSTALLED_HANDLERS_STATE_KEY, None) != options:
            raise ErrorConfigurationError(
                "ErrorRegistry is already installed with different normalization options"
            )
        return
    if installed is not None:
        msg = "a different ErrorRegistry is already installed on this application"
        raise ErrorConfigurationError(msg)
    if app.openapi_schema is not None:
        msg = "install ErrorRegistry before generating or caching OpenAPI"
        raise ErrorConfigurationError(msg)
    if app.middleware_stack is not None:
        raise ErrorConfigurationError("install ErrorRegistry before the app starts")

    registry.require_resolved()
    if (
        include_validation_error or include_unhandled_error
    ) and registry.type_base is None:
        msg = (
            "type_base is required when validation or unhandled-error normalization "
            "is enabled"
        )
        raise ErrorConfigurationError(msg)

    domain_classes = {
        *(error.exception for error in registry.errors),
    }
    for exception_class in domain_classes:
        _ensure_handler_available(app, exception_class)

    if include_http_exceptions:
        _ensure_default_or_available(app, HTTPException, http_exception_handler)
    if include_validation_error:
        _ensure_default_or_available(
            app, RequestValidationError, request_validation_exception_handler
        )
        _ensure_handler_available(app, ResponseValidationError)
    if include_unhandled_error:
        _ensure_handler_available(app, Exception)

    reserved: set[type[Exception]] = set()
    if include_http_exceptions:
        reserved.add(HTTPException)
    if include_validation_error:
        reserved.update((RequestValidationError, ResponseValidationError))
    if include_unhandled_error:
        reserved.add(Exception)
    overlap = domain_classes & reserved
    if overlap:
        names = ", ".join(sorted(exception.__qualname__ for exception in overlap))
        raise ErrorConfigurationError(
            f"domain exceptions conflict with enabled normalization handlers: {names}"
        )
    builtin_codes = set()
    if include_validation_error:
        builtin_codes.add("request_validation_error")
    if include_validation_error or include_unhandled_error:
        builtin_codes.add("internal_server_error")
    for error in registry.errors:
        if error.code in builtin_codes:
            raise ErrorConfigurationError(
                f"domain error code {error.code!r} conflicts with enabled normalization"
            )

    install_openapi(
        registry,
        app,
        include_validation_error=include_validation_error,
        include_http_exceptions=include_http_exceptions,
        include_internal_error=include_validation_error or include_unhandled_error,
    )
    domain_handler = _domain_handler(registry)
    for exception_class in domain_classes:
        app.add_exception_handler(exception_class, domain_handler)
    if include_http_exceptions:
        app.add_exception_handler(
            HTTPException, cast(ExceptionHandler, _http_exception_handler)
        )
    if include_validation_error:
        app.add_exception_handler(
            RequestValidationError, _request_validation_handler(registry)
        )
        app.add_exception_handler(
            ResponseValidationError, _response_validation_handler(registry)
        )
    if include_unhandled_error:
        app.add_exception_handler(Exception, _unhandled_handler(registry))
    setattr(app.state, INSTALLED_REGISTRY_STATE_KEY, registry)
    setattr(app.state, INSTALLED_HANDLERS_STATE_KEY, options)


def _domain_handler(registry: ErrorRegistry) -> ExceptionHandler:
    async def handler(
        connection: Request | WebSocket, exception: Exception
    ) -> Response | None:
        error = registry.resolve(exception)
        if error is None:
            raise exception
        type_uri = registry.type_uri_for(error)
        if type_uri is None:
            msg = f"error {error.code!r} has no resolved problem type URI"
            raise ErrorConfigurationError(msg)
        try:
            problem, headers = render_problem(error, exception, type_uri=type_uri)
        except Exception:
            logger.exception(
                "Error rendering callback failed",
                extra={
                    "code": error.code,
                    "exception_class": type(exception).__qualname__,
                },
            )
            return await _connection_response(
                connection,
                _internal_error_response(registry),
                code="internal_server_error",
            )
        return await _connection_response(
            connection,
            _problem_response(problem.as_dict(), error.status, headers=headers),
            code=error.code,
        )

    return cast(ExceptionHandler, handler)


async def _http_exception_handler(
    request: Request | WebSocket, exception: Exception
) -> Response | None:
    if not isinstance(exception, HTTPException):
        raise exception
    status = exception.status_code
    title = http_problem_title(status)
    detail = exception.detail if isinstance(exception.detail, str) else None
    payload: dict[str, JsonValue] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": f"http_{status}",
    }
    if detail is not None:
        payload["detail"] = detail
    return await _connection_response(
        request,
        _problem_response(payload, status, headers=exception.headers),
        code=f"http_{status}",
    )


async def _connection_response(
    connection: Request | WebSocket, response: Response, *, code: str
) -> Response | None:
    if not isinstance(connection, WebSocket):
        return response
    if connection.application_state == WebSocketState.DISCONNECTED:
        return None
    if (
        connection.application_state == WebSocketState.CONNECTING
        and "websocket.http.response" in connection.scope.get("extensions", {})
    ):
        await connection.send_denial_response(response)
    else:
        # A WebSocket close is a different transport from an HTTP response.
        # Preserve the stable problem code without inventing an application frame.
        await connection.close(
            code=1011 if response.status_code >= 500 else 1008, reason=code[:123]
        )
    return None


def _request_validation_handler(registry: ErrorRegistry) -> ExceptionHandler:
    async def handler(request: Request, exception: Exception) -> Response:
        del request
        if not isinstance(exception, RequestValidationError):
            raise exception
        errors: list[JsonValue] = []
        for error in exception.errors():
            errors.append(_validation_error(error))
        payload: dict[str, JsonValue] = {
            "type": _builtin_type(registry, "request_validation_error"),
            "title": "Request validation failed",
            "status": 422,
            "code": "request_validation_error",
            "errors": errors,
        }
        return _problem_response(payload, 422)

    return cast(ExceptionHandler, handler)


def _response_validation_handler(registry: ErrorRegistry) -> ExceptionHandler:
    async def handler(request: Request, exception: Exception) -> Response:
        del request
        if not isinstance(exception, ResponseValidationError):
            raise exception
        logger.error(
            "FastAPI response validation failed",
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        return _internal_error_response(registry)

    return cast(ExceptionHandler, handler)


def _unhandled_handler(registry: ErrorRegistry) -> ExceptionHandler:
    async def handler(request: Request, exception: Exception) -> Response:
        del request
        logger.error(
            "Unhandled application exception",
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        return _internal_error_response(registry)

    return cast(ExceptionHandler, handler)


def _validation_error(error: dict[str, object]) -> JsonValue:
    location = error.get("loc")
    parts = tuple(location) if isinstance(location, tuple | list) else ()
    source = parts[0] if parts else None
    result: dict[str, JsonValue] = {
        "code": str(error.get("type", "validation_error")),
        "detail": str(error.get("msg", "Invalid input")),
    }
    if source == "body":
        pointer = "#/" + "/".join(_escape_pointer(part) for part in parts[1:])
        result["pointer"] = pointer.rstrip("/") or "#"
    elif source in {"path", "query", "header", "cookie"} and len(parts) > 1:
        result["parameter"] = str(parts[-1])
        result["in"] = cast(str, source)
    return result


def _escape_pointer(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _internal_error_response(registry: ErrorRegistry) -> Response:
    return _problem_response(
        {
            "type": _builtin_type(registry, "internal_server_error"),
            "title": "Internal Server Error",
            "status": 500,
            "code": "internal_server_error",
        },
        500,
    )


def _builtin_type(registry: ErrorRegistry, code: str) -> str:
    if registry.type_base is None:
        msg = f"type_base is required to render built-in problem {code!r}"
        raise ErrorConfigurationError(msg)
    return f"{registry.type_base}/{code}"


def _problem_response(
    payload: dict[str, JsonValue],
    status: int,
    *,
    headers: Mapping[str, str] | None = None,
) -> Response:
    return JSONResponse(
        payload,
        status_code=status,
        headers=headers,
        media_type="application/problem+json",
    )


def _ensure_handler_available(app: FastAPI, exception_class: type[Exception]) -> None:
    if exception_class in app.exception_handlers:
        msg = (
            f"application already defines an exception handler for "
            f"{exception_class.__qualname__}"
        )
        raise ErrorConfigurationError(msg)


def _ensure_default_or_available(
    app: FastAPI,
    exception_class: type[Exception],
    known_default: object,
) -> None:
    existing = app.exception_handlers.get(exception_class)
    if existing is not None and existing is not known_default:
        msg = (
            f"application already defines an exception handler for "
            f"{exception_class.__qualname__}"
        )
        raise ErrorConfigurationError(msg)
