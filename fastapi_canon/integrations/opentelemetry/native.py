from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager, nullcontext
from typing import Any, override

from dishka import AsyncContainer
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.utils import (
    is_http_instrumentation_enabled,
    is_instrumentation_enabled,
    suppress_instrumentation,
)
from opentelemetry.trace import Span, SpanContext, SpanKind, Status, StatusCode
from starlette.types import ASGIApp, Lifespan, Receive, Scope, Send

from fastapi_canon.error.handlers import install_problem_observer
from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry.declarations import Telemetry
from fastapi_canon.integrations.opentelemetry.ports import excluded

_CONFIGURATION_FIELDS = frozenset(
    {
        "auto_configure",
        "tracing",
        "metrics",
        "logs",
        "operation_spans",
        "exclude",
        "tracer_provider",
        "meter_provider",
        "logger_provider",
    }
)


def validate_app(app: FastAPI) -> None:
    if not isinstance(getattr(app, "_telemetry", None), dict):
        raise FeatureConfigurationError(
            "Canon telemetry requires the native telemetry configuration of FastAPI >=0.165"
        )
    if not _CONFIGURATION_FIELDS.issubset(app._telemetry):
        raise FeatureConfigurationError(
            "unsupported FastAPI telemetry configuration; the native telemetry fields changed"
        )
    if app.middleware_stack is not None:
        raise FeatureConfigurationError("install telemetry before the app starts")


def install(
    app: FastAPI, options: Telemetry, container: AsyncContainer | None
) -> Lifespan[FastAPI] | None:
    """Configure native telemetry; with ``sdk``, return the lifespan that starts it.

    Until that lifespan resolves the container's SDK providers, native telemetry
    is off rather than falling back to global providers.
    """
    # FastAPI currently exposes telemetry only in its constructor. Updating the
    # existing dictionary keeps its NativeTelemetry/lifespan configuration shared.
    config = app._telemetry
    config.update(
        {
            "auto_configure": False,
            "operation_spans": options.operation_spans,
            "exclude": lambda scope: (
                _skip(scope, options)
                or (scope["type"] == "http" and not is_http_instrumentation_enabled())
            ),
        }
    )
    app.add_middleware(_RequestContext, options=options)
    if options.problem_details:
        install_problem_observer(app, _problem)
    if options.sdk is None:
        _use_providers(app, options)
        return None
    if container is None:
        raise FeatureConfigurationError("Telemetry(sdk=...) requires a container")
    config.update({"tracing": False, "metrics": False, "logs": False})

    @asynccontextmanager
    async def start(_app: FastAPI) -> AsyncGenerator[None, None]:
        owned = await container.get(Telemetry)
        _use_providers(app, owned)
        yield

    return start


def _use_providers(app: FastAPI, options: Telemetry) -> None:
    app._telemetry.update(
        {
            "tracing": options.tracing,
            "metrics": options.metrics,
            "logs": options.logs,
            "tracer_provider": (
                _ProblemProvider(options.tracer_provider)
                if options.problem_details
                else options.tracer_provider
            ),
            "meter_provider": options.meter_provider,
            "logger_provider": options.logger_provider,
        }
    )


def _problem(scope: Scope, code: str, status: int) -> None:
    span = scope.get("_canon_server_span")
    if isinstance(span, _ProblemSpan):
        span.set_attribute("canon.problem.code", code)
        if status >= 500:
            span.problem = code
            span.set_attribute("error.type", code)
            span.set_status(StatusCode.ERROR)


def _skip(scope: Scope, options: Telemetry) -> bool:
    key = f"_canon_telemetry_excluded_{id(options)}"
    if key not in scope:
        scope[key] = options.exclude is not None and options.exclude(scope)
    return bool(scope[key]) or (
        scope["type"] == "http" and (excluded.get() or not is_instrumentation_enabled())
    )


class _RequestContext:
    def __init__(self, app: ASGIApp, options: Telemetry) -> None:
        self.app = app
        self.options = options

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        skip = _skip(scope, self.options)
        exclusion = excluded.set(skip)
        http = scope["type"] == "http"
        scope["_canon_server_span"] = (
            None
            if skip or (http and not is_http_instrumentation_enabled())
            else trace.get_current_span()
        )
        try:
            with suppress_instrumentation() if http and skip else nullcontext():
                await self.app(scope, receive, send)
        finally:
            excluded.reset(exclusion)


class _ProblemProvider(trace.TracerProvider):
    """Delegate to an application/global provider without owning its lifecycle."""

    def __init__(self, provider: trace.TracerProvider | None) -> None:
        self.provider = provider

    @override
    def get_tracer(
        self,
        instrumenting_module_name: str,
        instrumenting_library_version: str | None = None,
        schema_url: str | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> trace.Tracer:
        return _ProblemTracer(
            trace.get_tracer(
                instrumenting_module_name,
                instrumenting_library_version,
                tracer_provider=self.provider,
                schema_url=schema_url,
                attributes=attributes,
            )
        )


class _ProblemTracer(trace.Tracer):
    def __init__(self, tracer: trace.Tracer) -> None:
        self.tracer = tracer

    def start_span(self, *args: Any, **kwargs: Any) -> Span:
        span = self.tracer.start_span(*args, **kwargs)
        kind = kwargs.get("kind", args[2] if len(args) > 2 else SpanKind.INTERNAL)
        return _ProblemSpan(span) if kind == SpanKind.SERVER else span

    def start_as_current_span(self, *args: Any, **kwargs: Any) -> Any:
        return self.tracer.start_as_current_span(*args, **kwargs)


class _ProblemSpan(Span):
    """Preserve the problem slug when FastAPI finalizes a 5xx server span."""

    def __init__(self, span: Span) -> None:
        self.span = span
        self.problem: str | None = None

    def end(self, end_time: int | None = None) -> None:
        if self.problem is not None:
            self.span.set_attribute("error.type", self.problem)
        self.span.end(end_time)

    def get_span_context(self) -> SpanContext:
        return self.span.get_span_context()

    def is_recording(self) -> bool:
        return self.span.is_recording()

    def set_attribute(self, key: str, value: Any) -> None:
        self.span.set_attribute(key, value)

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        self.span.set_attributes(attributes)

    def add_event(self, *args: Any, **kwargs: Any) -> None:
        self.span.add_event(*args, **kwargs)

    def add_link(self, *args: Any, **kwargs: Any) -> None:
        self.span.add_link(*args, **kwargs)

    def update_name(self, name: str) -> None:
        self.span.update_name(name)

    def set_status(
        self, status: Status | StatusCode, description: str | None = None
    ) -> None:
        self.span.set_status(status, description)

    def record_exception(self, *args: Any, **kwargs: Any) -> None:
        self.span.record_exception(*args, **kwargs)
