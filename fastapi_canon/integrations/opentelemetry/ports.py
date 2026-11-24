from collections.abc import Callable
from contextlib import nullcontext
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from typing import Any, cast

from dishka import Provider, Scope

try:
    from opentelemetry import metrics, trace
    from opentelemetry.instrumentation.utils import is_instrumentation_enabled
    from opentelemetry.trace import SpanKind, StatusCode
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Canon telemetry requires OpenTelemetry; install fastapi-canon[otel]",
        name=exc.name,
    ) from exc

from fastapi_canon.integration import ErrorLookup
from fastapi_canon.integrations.opentelemetry.declarations import (
    Instrumentation,
    PortRegistration,
    Telemetry,
    instrumented_methods,
)

type Registrations = tuple[PortRegistration, ...]

excluded: ContextVar[bool] = ContextVar("canon.telemetry.excluded", default=False)


class PortTelemetry:
    """The port tracer and duration histogram of one container's ``Telemetry``."""

    def __init__(self, telemetry: Telemetry) -> None:
        self.tracer = trace.get_tracer(
            "fastapi_canon", tracer_provider=telemetry.tracer_provider
        )
        self.histogram = (
            metrics.get_meter(
                "fastapi_canon", meter_provider=telemetry.meter_provider
            ).create_histogram("canon.port.duration", unit="s")
            if telemetry.port_metrics
            else None
        )


class _InstrumentedProxy:
    def __init__(self, instance: Any, runtime: PortTelemetry) -> None:
        self._instance = instance
        self._runtime = runtime

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

    def runtime(self) -> PortTelemetry:
        return self._runtime


class _InstrumentPort:
    def __init__(
        self,
        registrations: Registrations,
        options: Telemetry,
        errors: ErrorLookup | None,
    ) -> None:
        self._errors = errors
        self.spans = options.tracing and options.port_spans
        self.port_metrics = options.port_metrics
        self._proxies = {
            (spec, port): type(
                f"Instrumented{port.__name__}",
                (_InstrumentedProxy,),
                {
                    name: self._wrapper(spec, port, feature, name=name, method=method)
                    for name, method in instrumented_methods(port)
                },
            )
            for feature, port, spec in registrations
        }

    def __call__(
        self,
        spec: Instrumentation,
        port: type[Any],
        instance: Any,
        runtime: PortTelemetry,
    ) -> Any:
        if not self.spans and not self.port_metrics:
            return instance
        return self._proxies[(spec, port)](instance, runtime)

    def _wrapper(
        self,
        spec: Instrumentation,
        port: type[Any],
        feature: str,
        *,
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
            runtime = _InstrumentedProxy.runtime(proxy)
            if excluded.get() or not is_instrumentation_enabled():
                return await target(*args, **kwargs)
            attributes = {
                **registered,
                "canon.adapter": _InstrumentedProxy.adapter_name(proxy),
            }
            started = perf_counter()
            outcome = dict(registered)
            with (
                runtime.tracer.start_as_current_span(
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
                    expected = isinstance(error, spec.expected) or self._declared(error)
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
                    if runtime.histogram is not None:
                        runtime.histogram.record(perf_counter() - started, outcome)

        return call

    def _declared(self, error: Exception) -> bool:
        # A declared client error is an expected outcome, not a failed operation.
        declared = self._errors.resolve(error) if self._errors is not None else None
        return declared is not None and declared.status < 500


def create_instrumentation_providers(
    registrations: Registrations,
    options: Telemetry,
    errors: ErrorLookup | None = None,
) -> tuple[Provider, ...]:
    """Decorate each ``(feature, port, declaration)`` binding with a proxy.

    Exceptions declared as client errors (status below 500) in *errors* count as
    expected, in addition to each declaration's ``expected`` types.
    """
    observer = _InstrumentPort(registrations, options, errors)
    runtimes = tuple(
        _runtime_provider(component)
        for component in dict.fromkeys(spec.component for _, _, spec in registrations)
    )
    return (
        *runtimes,
        *(_provider(spec, port, observer) for _, port, spec in registrations),
    )


def _runtime_provider(component: str) -> Provider:
    provider = Provider(component=component)
    provider.provide(PortTelemetry, scope=Scope.APP)
    return provider


def _provider(
    spec: Instrumentation, port: type[Any], observer: _InstrumentPort
) -> Provider:
    def wrap(instance: Any, runtime: PortTelemetry) -> Any:
        return observer(spec, port, instance, runtime)

    wrap.__annotations__ = {"instance": port, "runtime": PortTelemetry, "return": port}
    provider = Provider(component=spec.component)
    provider.decorate(wrap, provides=port)
    return provider
