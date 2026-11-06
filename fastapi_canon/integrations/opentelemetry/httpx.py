"""Lifetime-scoped HTTPX/HTTPX2 instrumentation using the public contrib API."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from typing import TYPE_CHECKING

from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry.declarations import Telemetry
from fastapi_canon.integrations.opentelemetry.resources import _claim

if TYPE_CHECKING:
    from opentelemetry.trace import TracerProvider


@contextmanager
def instrument_httpx[T](
    client: T, telemetry: Telemetry, *, capture_url: bool = False
) -> Iterator[T]:
    """Instrument only this client; detach without closing it or its providers.

    URLs, headers, payloads and exception messages are omitted by default.
    ``capture_url=True`` permits upstream URL attributes, including query strings.
    Do not combine instrumentors on the same client or mutate it during this scope.
    """
    if not telemetry.tracing and not telemetry.metrics:
        yield client
        return
    from opentelemetry.metrics import NoOpMeterProvider
    from opentelemetry.trace import NoOpTracerProvider

    from fastapi_canon.integrations.opentelemetry.httpx_tracing import _HttpProvider

    adapter = import_module("opentelemetry.instrumentation.httpx")
    instrumentor = None
    for module_name, instrumentor_name in (
        ("httpx", "HTTPXClientInstrumentor"),
        ("httpx2", "HTTPX2ClientInstrumentor"),
    ):
        try:
            module = import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name != module_name:
                raise
            continue
        if isinstance(client, (module.Client, module.AsyncClient)):
            instrumentor = getattr(adapter, instrumentor_name)
            break
    if instrumentor is None:
        raise FeatureConfigurationError(
            "instrument_httpx requires an HTTPX or HTTPX2 client"
        )
    # The public per-client API returns no ownership handle. This upstream marker
    # is checked only to avoid removing instrumentation installed by another owner.
    if getattr(client, "_is_instrumented_by_opentelemetry", False):
        raise FeatureConfigurationError("the HTTP client is already instrumented")
    provider: TracerProvider = (
        _HttpProvider(telemetry.tracer_provider, capture_url=capture_url)
        if telemetry.tracing
        else NoOpTracerProvider()
    )
    with _claim(client):
        try:
            instrumentor.instrument_client(
                client,
                tracer_provider=provider,
                meter_provider=telemetry.meter_provider
                if telemetry.metrics
                else NoOpMeterProvider(),
            )
            yield client
        finally:
            instrumentor.uninstrument_client(client)
