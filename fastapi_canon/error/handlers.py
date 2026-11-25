from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import cast

from fastapi import FastAPI, Request, WebSocket
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.middleware.body_limit import MAX_BODY_SIZE_SCOPE_KEY
from starlette.responses import Response
from starlette.types import ASGIApp, ExceptionHandler, Message, Receive, Scope, Send
from starlette.websockets import WebSocketState

from fastapi_canon.error.contracts import (
    INSTALLED_HANDLERS_STATE_KEY,
    INSTALLED_REGISTRY_STATE_KEY,
    ErrorCatalog,
)
from fastapi_canon.error.openapi import install_openapi
from fastapi_canon.error.rendering import render_problem
from fastapi_canon.error.types import JsonValue, http_problem_title
from fastapi_canon.errors import ErrorConfigurationError

logger = logging.getLogger(__name__)
_PROBLEM_OBSERVER_STATE_KEY = "_canon_problem_telemetry"

type ProblemObserver = Callable[[Scope, str, int], None]


def install_problem_observer(app: FastAPI, observer: ProblemObserver) -> None:
    """Attach an optional observer to problem responses rendered by this adapter."""
    setattr(app.state, _PROBLEM_OBSERVER_STATE_KEY, observer)


def install_handlers(
    registry: ErrorCatalog,
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
        exception_names = sorted(exception.__qualname__ for exception in overlap)
        names = ", ".join(exception_names)
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
        build = app.build_middleware_stack

        def build_with_body_limit_problems() -> ASGIApp:
            return _BodyLimitProblems(build())

        app.build_middleware_stack = build_with_body_limit_problems  # type: ignore[method-assign]  # Wrap FastAPI's lazy stack outside the body limiter.
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


class _BodyLimitProblems:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        problem: Response | None = None
        received_bytes = 0

        async def count_body() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
            return message

        async def send_problem(message: Message) -> None:
            nonlocal problem
            if message["type"] == "http.response.start" and message["status"] == 413:
                if _body_exceeds_limit(scope, received_bytes):
                    # The active limiter sends this response outside exception handling.
                    problem = _http_problem_response(
                        Request(scope), HTTPException(413, "Content Too Large")
                    )
                    headers = [
                        (name, value)
                        for name, value in message.get("headers", ())
                        if name
                        not in (b"content-type", b"content-length", b"content-encoding")
                    ]
                    message = {**message, "headers": [*headers, *problem.raw_headers]}
            elif problem is not None and message["type"] == "http.response.body":
                if not message.get("more_body", False):
                    await send({"type": "http.response.body", "body": problem.body})
                return
            await send(message)

        await self.app(scope, count_body, send_problem)


def _body_exceeds_limit(scope: Scope, received_bytes: int) -> bool:
    limit = scope.get(MAX_BODY_SIZE_SCOPE_KEY)
    if not isinstance(limit, int):
        return False
    if received_bytes > limit:
        return True
    try:
        return int(Headers(scope=scope).get("content-length", "0")) > limit
    except ValueError:
        return False


def _domain_handler(registry: ErrorCatalog) -> ExceptionHandler:
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
        # User renderers may fail arbitrarily; serve the safe internal-error response
        # instead.
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
                _internal_error_response(registry, connection),
                code="internal_server_error",
            )
        return await _connection_response(
            connection,
            _problem_response(
                connection, problem.as_dict(), error.status, headers=headers
            ),
            code=error.code,
        )

    return cast(ExceptionHandler, handler)


async def _http_exception_handler(
    request: Request | WebSocket, exception: Exception
) -> Response | None:
    if not isinstance(exception, HTTPException):
        raise exception
    status = exception.status_code
    if not is_body_allowed_for_status_code(status):
        return await _connection_response(
            request,
            Response(status_code=status, headers=exception.headers),
            code=f"http_{status}",
        )
    return await _connection_response(
        request, _http_problem_response(request, exception), code=f"http_{status}"
    )


def _http_problem_response(
    request: Request | WebSocket, exception: HTTPException
) -> Response:
    status = exception.status_code
    payload: dict[str, JsonValue] = {
        "type": "about:blank",
        "title": http_problem_title(status),
        "status": status,
        "code": f"http_{status}",
    }
    if isinstance(exception.detail, str):
        payload["detail"] = exception.detail
    return _problem_response(request, payload, status, headers=exception.headers)


def _request_validation_handler(registry: ErrorCatalog) -> ExceptionHandler:
    async def handler(request: Request | WebSocket, exception: Exception) -> Response:
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
        return _problem_response(request, payload, 422)

    return cast(ExceptionHandler, handler)


def _response_validation_handler(registry: ErrorCatalog) -> ExceptionHandler:
    async def handler(request: Request | WebSocket, exception: Exception) -> Response:
        if not isinstance(exception, ResponseValidationError):
            raise exception
        logger.error(
            "FastAPI response validation failed",
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        return _internal_error_response(registry, request)

    return cast(ExceptionHandler, handler)


def _unhandled_handler(registry: ErrorCatalog) -> ExceptionHandler:
    async def handler(request: Request | WebSocket, exception: Exception) -> Response:
        # ServerErrorMiddleware re-raises after sending this response; Uvicorn
        # owns that traceback. Only exceptions consumed here need a local log.
        return _internal_error_response(registry, request)

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
        result["parameter"] = str(parts[1])
        result["in"] = cast(str, source)
    return result


def _escape_pointer(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _internal_error_response(
    registry: ErrorCatalog, request: Request | WebSocket
) -> Response:
    return _problem_response(
        request,
        {
            "type": _builtin_type(registry, "internal_server_error"),
            "title": "Internal Server Error",
            "status": 500,
            "code": "internal_server_error",
        },
        500,
    )


def _builtin_type(registry: ErrorCatalog, code: str) -> str:
    if registry.type_base is None:
        msg = f"type_base is required to render built-in problem {code!r}"
        raise ErrorConfigurationError(msg)
    return f"{registry.type_base}/{code}"


def _problem_response(
    request: Request | WebSocket,
    payload: dict[str, JsonValue],
    status: int,
    *,
    headers: Mapping[str, str] | None = None,
) -> Response:
    response = JSONResponse(
        payload,
        status_code=status,
        headers=headers,
        media_type="application/problem+json",
    )
    request.scope["_canon_problem_code"] = str(payload["code"])
    request.scope["_canon_problem_status"] = status
    enrich = cast(
        ProblemObserver | None,
        getattr(request.app.state, _PROBLEM_OBSERVER_STATE_KEY, None),
    )
    if enrich is not None:
        enrich(request.scope, str(payload["code"]), status)
    return response


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
