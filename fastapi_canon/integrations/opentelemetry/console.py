import sys
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry.declarations import (
    SdkFactory,
    SdkProviders,
)


def console(*, service_name: str) -> SdkFactory:
    """Print spans to standard output, for ``Telemetry(sdk=console(...))``.

    Each entry creates a tracer provider with the ``service.name`` resource and
    a synchronous console exporter; exit flushes and shuts it down. Metrics and
    logs keep using the global providers. Entering requires ``opentelemetry-sdk``.
    """
    if not isinstance(service_name, str) or not service_name.strip():
        raise FeatureConfigurationError("service_name must be a non-empty string")

    @contextmanager
    def sdk() -> Iterator[SdkProviders]:
        try:
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                ConsoleSpanExporter,
                SimpleSpanProcessor,
            )
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "console() requires the OpenTelemetry SDK; install opentelemetry-sdk",
                name=exc.name,
            ) from exc

        provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: service_name})
        )
        try:
            provider.add_span_processor(
                SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stdout))
            )
            yield SdkProviders(tracer_provider=provider)
        finally:
            provider.shutdown()

    return sdk
