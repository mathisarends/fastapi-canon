"""Port instrumentation loaded only by an enabled telemetry composition."""

from collections.abc import Callable
from contextlib import nullcontext
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from typing import Any, cast

from dishka import Provider

try:
    from opentelemetry import metrics, trace
    from opentelemetry.instrumentation.utils import is_instrumentation_enabled
    from opentelemetry.trace import SpanKind, StatusCode
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Canon telemetry requires OpenTelemetry; install fastapi-canon[otel]",
        name=exc.name,
    ) from exc

from fastapi_canon.integrations.opentelemetry.declarations import (
    Instrumentation,
    Telemetry,
    instrumented_methods,
)

type Registrations = tuple[tuple[str, type[Any], Instrumentation], ...]
"""``(feature name, port, declaration)`` triples in composition order."""

excluded: ContextVar[bool] = ContextVar("canon.telemetry.excluded", default=False)


class _InstrumentedProxy:
    def __init__(self, instance: Any) -> None:
        self._instance = instance

    @property  # type: ignore[misc]  # Proxy type reporting is intentionally read-only.
    def __class__(self) -> type[Any]:
        # isinstance (including Pydantic's arbitrary-type validation) consults
        # __class__. Preserve the actual adapter's type without unwrapping it.
        return type(self._instance)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._instance, name)

    def target_method(self, name: str) -> Callable[..., Any]:
        return cast(Callable[..., Any], getattr(self._instance, name))

    def adapter_name(self) -> str:
        return type(self._instance).__name__


class _InstrumentPort:
    def __init__(self, registrations: Registrations, options: Telemetry) -> None:
        self.tracer = trace.get_tracer(
            "fastapi_canon", tracer_provider=options.tracer_provider
        )
        self._histogram = (
            metrics.get_meter(
                "fastapi_canon", meter_provider=options.meter_provider
            ).create_histogram("canon.port.duration", unit="s")
            if options.port_metrics
            else None
        )
        self.spans = options.tracing and options.port_spans
        self._proxies = {
            (spec, port): type(
                f"Instrumented{port.__name__}",
                (_InstrumentedProxy,),
                {
                    name: self._wrapper(spec, port, feature, name, method)
                    for name, method in instrumented_methods(port)
                },
            )
            for feature, port, spec in registrations
        }

    def __call__(self, spec: Instrumentation, port: type[Any], instance: Any) -> Any:
        if not self.spans and self._histogram is None:
            return instance
        return self._proxies[(spec, port)](instance)

    def _wrapper(
        self,
        spec: Instrumentation,
        port: type[Any],
        feature: str,
        name: str,
        method: Callable[..., Any],
    ) -> Callable[..., Any]:
        registered: dict[str, str] = {
            "canon.feature": feature,
            "canon.port": port.__name__,
            "code.function.name": f"{port.__name__}.{name}",
        }

        @wraps(method)
        async def call(proxy: _InstrumentedProxy, *args: Any, **kwargs: Any) -> Any:
            # Port methods can have the same names as proxy operations.
            target = _InstrumentedProxy.target_method(proxy, name)
            if excluded.get() or not is_instrumentation_enabled():
                return await target(*args, **kwargs)
            attributes = {
                **registered,
                "canon.adapter": _InstrumentedProxy.adapter_name(proxy),
            }
            started = perf_counter()
            outcome = dict(registered)
            with (
                self.tracer.start_as_current_span(
                    registered["code.function.name"],
                    kind=SpanKind.INTERNAL,
                    attributes=attributes,
                    record_exception=False,
                    set_status_on_exception=False,
                )
                if self.spans
                else nullcontext(None)
            ) as span:
                try:
                    return await target(*args, **kwargs)
                except Exception as error:
                    error_type = type(error).__name__
                    expected = isinstance(error, spec.expected)
                    if not expected:
                        outcome["error.type"] = error_type
                    if span is not None:
                        # Exception messages/stack traces can contain arguments.
                        span.add_event(
                            "canon.port.expected_exception"
                            if expected
                            else "exception",
                            {"exception.type": error_type},
                        )
                        if not expected:
                            span.set_attribute("error.type", error_type)
                            span.set_status(StatusCode.ERROR)
                    raise
                finally:
                    if self._histogram is not None:
                        self._histogram.record(perf_counter() - started, outcome)

        return call


def create_instrumentation_providers(
    registrations: Registrations, options: Telemetry
) -> tuple[Provider, ...]:
    """Decorate each ``(feature, port, declaration)`` binding with a proxy."""
    observer = _InstrumentPort(registrations, options)
    return tuple(_provider(spec, port, observer) for _, port, spec in registrations)


def _provider(
    spec: Instrumentation, port: type[Any], observer: _InstrumentPort
) -> Provider:
    def wrap(instance: Any) -> Any:
        return observer(spec, port, instance)

    wrap.__annotations__ = {"instance": port, "return": port}
    provider = Provider(component=spec.component)
    provider.decorate(wrap, provides=port)
    return provider
