import asyncio
import subprocess
import sys
import textwrap
from collections.abc import Callable
from importlib import import_module
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.trace import StatusCode
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastapi_canon import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry import Telemetry
from fastapi_canon.integrations.opentelemetry.httpx import instrument_httpx
from tests.fixtures.telemetry import SpanCapture


@pytest.mark.parametrize("library", ["httpx", "httpx2"])
@pytest.mark.parametrize("capture_url", [False, True])
async def test_async_http_client_ownership_privacy_propagation_and_cleanup(
    library: str,
    capture_url: bool,
) -> None:
    module = import_module(library)
    received: list[str] = []

    async def endpoint(request: Request) -> JSONResponse:
        received.append(request.headers.get("traceparent", ""))
        if request.url.path == "/failure":
            raise ValueError("secret-payload")
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/{path}", endpoint)])
    global_provider = trace.get_tracer_provider()
    with SpanCapture() as capture:
        options = Telemetry(tracer_provider=capture.provider, metrics=False)
        async with (
            asyncio.timeout(5),
            module.AsyncClient(
                transport=module.ASGITransport(app=app), base_url="http://test"
            ) as client,
            module.AsyncClient(
                transport=module.ASGITransport(app=app), base_url="http://test"
            ) as other,
        ):
            with instrument_httpx(client, options, capture_url=capture_url) as bound:
                assert bound is client
                assert (await other.get("/plain")).status_code == 200
                assert not capture.spans
                assert (
                    await client.get(
                        "/ok?token=secret-query", headers={"secret": "secret-header"}
                    )
                ).json() == {"ok": True}
                with (
                    pytest.raises(FeatureConfigurationError, match="already"),
                    instrument_httpx(client, options),
                ):
                    pass
                with pytest.raises(ValueError, match="secret-payload"):
                    await client.get("/failure")
                with suppress_instrumentation():
                    assert (await client.get("/suppressed")).status_code == 200
            assert (await client.get("/after")).status_code == 200
            assert not client.is_closed
        assert len(capture.spans) == 2
        assert received[0] == "" and received[1].startswith("00-")
        success, failure = capture.spans
        assert ("secret-query" in str(success.attributes)) is capture_url
        assert "secret-header" not in str(success.attributes)
        assert failure.status.status_code is StatusCode.ERROR
        assert "secret-payload" not in str(failure.attributes) + str(
            failure.events
        ) + str(failure.status.description)
        assert not failure.events
        with capture.provider.get_tracer("owner").start_as_current_span("owner"):
            pass
        assert capture.one("owner").name == "owner"
    assert trace.get_tracer_provider() is global_provider


@pytest.mark.parametrize("library", ["httpx", "httpx2"])
def test_sync_http_client_detaches_after_exception_and_preserves_foreign_instrumentation(
    library: str,
) -> None:
    module = import_module(library)
    adapter = import_module("opentelemetry.instrumentation.httpx")
    instrumentor = getattr(
        adapter,
        "HTTPX2ClientInstrumentor"
        if library == "httpx2"
        else "HTTPXClientInstrumentor",
    )

    def application(
        environ: dict[str, Any], start_response: Callable[..., Any]
    ) -> list[bytes]:
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    with (
        SpanCapture() as capture,
        module.Client(
            transport=module.WSGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        options = Telemetry(tracer_provider=capture.provider, metrics=False)
        with (
            pytest.raises(ValueError, match="owner failure"),
            instrument_httpx(client, options),
        ):
            assert client.get("/").text == "ok"
            raise ValueError("owner failure")
        assert client.get("/").status_code == 200
        assert len(capture.spans) == 1
        instrumentor.instrument_client(client, tracer_provider=capture.provider)
        try:
            with (
                pytest.raises(FeatureConfigurationError, match="already instrumented"),
                instrument_httpx(client, options),
            ):
                pass
            assert client.get("/").status_code == 200
            assert len(capture.spans) == 2
        finally:
            instrumentor.uninstrument_client(client)


@pytest.mark.parametrize(
    ("tracing", "metrics"), [(True, False), (False, True), (False, False)]
)
async def test_http_adapter_respects_independent_signal_settings(
    tracing: bool, metrics: bool
) -> None:
    import httpx2

    async def endpoint(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        with SpanCapture() as capture:
            options = Telemetry(
                tracer_provider=capture.provider,
                meter_provider=meter,
                tracing=tracing,
                metrics=metrics,
            )
            app = Starlette(routes=[Route("/", endpoint)])
            async with (
                asyncio.timeout(5),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                with instrument_httpx(client, options):
                    assert (await client.get("/")).status_code == 200
            assert len(capture.spans) == int(tracing)
            data = reader.get_metrics_data()
            assert (data is not None) is metrics
    finally:
        meter.shutdown()


async def test_http_adapter_detaches_after_task_cancellation() -> None:
    import httpx2

    started = asyncio.Event()

    async def endpoint(request: Request) -> JSONResponse:
        if request.url.path == "/cancel":
            started.set()
            await asyncio.wait_for(asyncio.Event().wait(), timeout=3)
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/{path}", endpoint)])
    with SpanCapture() as capture:
        options = Telemetry(tracer_provider=capture.provider, metrics=False)
        async with (
            asyncio.timeout(5),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):

            async def request() -> None:
                with instrument_httpx(client, options):
                    await client.get("/cancel")

            task = asyncio.create_task(request())
            try:
                await asyncio.wait_for(started.wait(), timeout=3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            with instrument_httpx(client, options):
                assert (await client.get("/ok")).status_code == 200
        assert len(capture.spans) == 2
        assert capture.spans[0].status.status_code is StatusCode.UNSET
        assert not capture.spans[0].events


def test_disabled_adapters_and_standalone_configuration_import_no_optional_runtimes() -> (
    None
):
    script = textwrap.dedent("""
        import asyncio
        import sys
        from importlib.abc import MetaPathFinder
        from dishka import make_async_container
        import fastapi
        for name in tuple(sys.modules):
            if name.startswith('opentelemetry'):
                del sys.modules[name]
        class BlockOptional(MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split('.')[0] in {'opentelemetry', 'httpx', 'httpx2', 'sqlalchemy'}:
                    raise ModuleNotFoundError('optional runtime unavailable', name=fullname)
        sys.meta_path.insert(0, BlockOptional())
        from fastapi_canon.integrations.opentelemetry import Telemetry
        from fastapi_canon.integrations.opentelemetry.httpx import instrument_httpx
        from fastapi_canon.integrations.opentelemetry.sqlalchemy import SqlAlchemyInstrumentation, instrument_sqlalchemy
        options = Telemetry.disabled()
        resource = object()
        with instrument_httpx(resource, options) as client, instrument_sqlalchemy(resource, options) as engine:
            assert client is engine is resource
        with SqlAlchemyInstrumentation(telemetry=options)(resource) as engine:
            assert engine is resource
        async def check():
            container = make_async_container(options.provider())
            try:
                assert await container.get(Telemetry) is options
            finally:
                await container.close()
        asyncio.run(asyncio.wait_for(check(), 5))
        assert not any(name.startswith(('opentelemetry', 'httpx', 'sqlalchemy')) for name in sys.modules)
    """)
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stdout + result.stderr
