"""Opt-in HTTP completion records, outside the application's error middleware."""

import logging
from time import perf_counter

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("fastapi_canon.access")


def install_request_logging(app: FastAPI) -> None:
    build = app.build_middleware_stack

    def build_with_logging() -> ASGIApp:
        return _RequestLogging(build())

    # Wrap the lazily built stack so later add_middleware calls still work and
    # ServerErrorMiddleware's actual 500 response passes through our send hook.
    app.build_middleware_stack = build_with_logging  # type: ignore[method-assign]  # Install the per-app lazy stack wrapper.


class _RequestLogging:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        correlation = _trace_fields()
        status: int | None = None
        completed = False

        def record(event: str) -> None:
            fields: dict[str, object] = {
                "event": event,
                "method": scope["method"],
                "route": _route_template(scope),
                "status": status,
                "duration_ms": (perf_counter() - started) * 1000,
                **correlation,
            }
            problem = scope.get("_canon_problem_code")
            if problem is not None and status == scope.get("_canon_problem_status"):
                fields["problem_code"] = problem
            logger.info(event, extra=fields)

        async def observe_send(message: Message) -> None:
            nonlocal status, completed
            await send(message)
            if message["type"] == "http.response.start":
                status = message["status"]
            elif not completed and (
                (
                    message["type"] == "http.response.body"
                    and not message.get("more_body", False)
                )
                or message["type"] == "http.response.pathsend"
            ):
                completed = True
                record("http.request.completed")

        try:
            await self.app(scope, receive, observe_send)
        finally:
            if not completed:
                record("http.request.aborted")


def _trace_fields() -> dict[str, str]:
    try:
        from opentelemetry import trace
    except ImportError:
        return {}
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
    }


def _route_template(scope: Scope) -> str | None:
    route = scope.get("route")
    if route is None:
        return None
    # FastAPI >=0.140 shares included route objects. The effective scope context
    # carries their complete prefix; older versions copy the prefixed route.
    context = scope.get("fastapi", {}).get("effective_route_context")
    path = getattr(context, "path_format", None) or getattr(route, "path_format", None)
    return path if isinstance(path, str) else None
