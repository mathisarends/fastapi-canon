import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import httpx2
import pytest
from dishka import FromDishka, Provider, Scope
from dishka.integrations.fastapi import inject
from fastapi import FastAPI
from opentelemetry.instrumentation.httpx import HTTPX2ClientInstrumentor
from opentelemetry.instrumentation.utils import (
    is_http_instrumentation_enabled,
    is_instrumentation_enabled,
    suppress_http_instrumentation,
    suppress_instrumentation,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import Histogram, InMemoryMetricReader
from opentelemetry.trace import SpanKind
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Scope as ASGIScope

from fastapi_canon import CanonRouter, Composition, Feature
from fastapi_canon.integrations.opentelemetry import Telemetry, instrument
from tests.fixtures.telemetry import SpanCapture


class Repository:
    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def get(self, name: str) -> str:
        response = await self.client.get(f"/resource/{name}")
        response.raise_for_status()
        return str(response.json()["value"])


class UseCase:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    async def __call__(self, name: str) -> str:
        return await self.repository.get(name)


@dataclass
class HttpScenario:
    feature: Feature
    observations: list[tuple[str, bool]]
    cancel_started: asyncio.Event


@pytest.fixture
def telemetry() -> Iterator[tuple[SpanCapture, MeterProvider, InMemoryMetricReader]]:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        with SpanCapture() as capture:
            yield capture, meter, reader
    finally:
        meter.shutdown()


@pytest.fixture
async def scenario(
    telemetry: tuple[SpanCapture, MeterProvider, InMemoryMetricReader],
) -> AsyncIterator[HttpScenario]:
    capture, meter, _ = telemetry
    observations: list[tuple[str, bool]] = []
    barrier = asyncio.Barrier(2)
    cancel_started = asyncio.Event()

    async def resource(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        observations.append((name, is_instrumentation_enabled()))
        try:
            if name.endswith("concurrent"):
                await asyncio.wait_for(barrier.wait(), timeout=5)
            if name == "excluded-cancel":
                cancel_started.set()
                await asyncio.wait_for(asyncio.Event().wait(), timeout=5)
            return JSONResponse({"value": name})
        finally:
            observations.append((name, is_instrumentation_enabled()))

    downstream = Starlette(routes=[Route("/resource/{name}", resource)])
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=downstream),
        base_url="http://downstream.test",
        timeout=5,
    ) as outbound:
        HTTPX2ClientInstrumentor.instrument_client(
            # OTel's HTTPX2 API still annotates clients as HTTPX.
            outbound,  # type: ignore[arg-type]
            tracer_provider=capture.provider,
            meter_provider=meter,
        )
        try:
            provider = Provider()
            provider.provide(
                lambda: outbound, provides=httpx2.AsyncClient, scope=Scope.APP
            )
            provider.provide(Repository, scope=Scope.REQUEST)
            provider.provide(UseCase, scope=Scope.REQUEST)
            router = CanonRouter()

            @router.get("/{name}")
            @inject
            async def endpoint(name: str, use_case: FromDishka[UseCase]) -> str:
                result = await use_case(name)
                if name == "excluded-error":
                    raise RuntimeError("request failed")
                return result

            yield HttpScenario(
                Feature(
                    name="http",
                    providers=[provider],
                    routers=[router],
                    instrumentation=[instrument(UseCase), instrument(Repository)],
                ),
                observations,
                cancel_started,
            )
        finally:
            # OTel's HTTPX2 API still annotates clients as HTTPX.
            HTTPX2ClientInstrumentor.uninstrument_client(outbound)  # type: ignore[arg-type]


@pytest.mark.parametrize("native_signals", [True, False])
async def test_http_exclusion_suppresses_use_case_ports_and_httpx(
    scenario: HttpScenario,
    telemetry: tuple[SpanCapture, MeterProvider, InMemoryMetricReader],
    native_signals: bool,
) -> None:
    capture, meter, reader = telemetry
    calls: list[str] = []

    def exclude(scope: ASGIScope) -> bool:
        calls.append(scope["path"])
        return str(scope["path"]).startswith("/excluded")

    app = Composition(
        scenario.feature,
        telemetry=Telemetry(
            tracer_provider=capture.provider,
            meter_provider=meter,
            tracing=native_signals,
            metrics=native_signals,
            port_metrics=True,
            logs=False,
            exclude=exclude,
        ),
    ).apply(FastAPI())
    async with (
        asyncio.timeout(5),
        app.router.lifespan_context(app),
        _client(app) as client,
    ):
        assert (await client.get("/excluded")).json() == "excluded"
        assert not capture.spans
        assert _histogram_counts(reader) == {}
        assert is_instrumentation_enabled()
        assert (await client.get("/included")).json() == "included"

    assert calls == ["/excluded", "/included"]
    assert scenario.observations == [
        ("excluded", False),
        ("excluded", False),
        ("included", True),
        ("included", True),
    ]
    if native_signals:
        capture.assert_tree(
            "GET /{name}",
            [
                (
                    "fastapi.endpoint",
                    [("UseCase.__call__", [("Repository.get", ["GET"])])],
                )
            ],
        )
    else:
        assert [span.name for span in capture.spans] == ["GET"]
    counts = _histogram_counts(reader)
    assert counts["canon.port.duration"] == 2
    assert any(counts.get(name) == 1 for name in _HTTP_CLIENT_METRICS)


async def test_inherited_suppression_cannot_be_reenabled_by_http_exclude(
    scenario: HttpScenario,
    telemetry: tuple[SpanCapture, MeterProvider, InMemoryMetricReader],
) -> None:
    capture, meter, reader = telemetry
    app = _application(scenario.feature, capture, meter)
    async with (
        asyncio.timeout(5),
        app.router.lifespan_context(app),
        _client(app) as client,
    ):
        with suppress_instrumentation():
            assert (await client.get("/suppressed")).json() == "suppressed"
            assert not is_instrumentation_enabled()
        assert not capture.spans
        assert _histogram_counts(reader) == {}
        assert is_instrumentation_enabled()
        assert (await client.get("/included")).json() == "included"

    assert scenario.observations[:2] == [("suppressed", False), ("suppressed", False)]
    assert capture.one("UseCase.__call__").name == "UseCase.__call__"
    assert _histogram_counts(reader)["http.server.request.duration"] == 1
    assert _histogram_counts(reader)["canon.port.duration"] == 2


async def test_http_only_suppression_keeps_port_instrumentation(
    scenario: HttpScenario,
    telemetry: tuple[SpanCapture, MeterProvider, InMemoryMetricReader],
) -> None:
    capture, meter, reader = telemetry
    app = _application(scenario.feature, capture, meter)
    async with (
        asyncio.timeout(5),
        app.router.lifespan_context(app),
        _client(app) as client,
    ):
        with suppress_http_instrumentation():
            assert (await client.get("/http-only")).json() == "http-only"
            assert is_instrumentation_enabled()
            assert not is_http_instrumentation_enabled()
        assert is_http_instrumentation_enabled()

    assert [span.name for span in capture.spans] == [
        "Repository.get",
        "UseCase.__call__",
    ]
    capture.assert_tree("UseCase.__call__", ["Repository.get"])
    assert _histogram_counts(reader) == {"canon.port.duration": 2}


async def test_http_exclusion_is_restored_after_an_exception(
    scenario: HttpScenario,
    telemetry: tuple[SpanCapture, MeterProvider, InMemoryMetricReader],
) -> None:
    capture, meter, reader = telemetry
    app = _application(scenario.feature, capture, meter)
    async with (
        asyncio.timeout(5),
        app.router.lifespan_context(app),
        _client(app) as client,
    ):
        assert (await client.get("/excluded-error")).status_code == 500
        assert is_instrumentation_enabled()
        assert not capture.spans
        assert _histogram_counts(reader) == {}
        assert (await client.get("/included")).json() == "included"

    assert _histogram_counts(reader)["canon.port.duration"] == 2
    assert capture.one("GET").kind is SpanKind.CLIENT


async def test_http_exclusion_is_restored_after_task_cancellation(
    scenario: HttpScenario,
    telemetry: tuple[SpanCapture, MeterProvider, InMemoryMetricReader],
) -> None:
    capture, meter, reader = telemetry
    app = _application(scenario.feature, capture, meter)
    async with (
        asyncio.timeout(5),
        app.router.lifespan_context(app),
        _client(app) as client,
    ):

        async def cancel_then_request() -> httpx2.Response:
            with pytest.raises(asyncio.CancelledError):
                await client.get("/excluded-cancel")
            assert is_instrumentation_enabled()
            assert not capture.spans
            assert _histogram_counts(reader) == {}
            return await client.get("/included")

        async with asyncio.TaskGroup() as tasks:
            task = tasks.create_task(cancel_then_request())
            await asyncio.wait_for(scenario.cancel_started.wait(), timeout=3)
            task.cancel()
        assert task.result().json() == "included"

    assert scenario.observations == [
        ("excluded-cancel", False),
        ("excluded-cancel", False),
        ("included", True),
        ("included", True),
    ]
    assert _histogram_counts(reader)["canon.port.duration"] == 2
    assert capture.one("GET /{name}").name == "GET /{name}"


async def test_parallel_applications_reuse_features_without_exclusion_leaks(
    scenario: HttpScenario,
    telemetry: tuple[SpanCapture, MeterProvider, InMemoryMetricReader],
) -> None:
    capture, meter, reader = telemetry
    excluded = Composition(
        scenario.feature,
        telemetry=Telemetry(
            tracer_provider=capture.provider,
            meter_provider=meter,
            port_metrics=True,
            logs=False,
            exclude=lambda scope: True,
        ),
    ).apply(FastAPI())
    included = _application(scenario.feature, capture, meter)
    async with (
        asyncio.timeout(5),
        excluded.router.lifespan_context(excluded),
        included.router.lifespan_context(included),
        _client(excluded) as first,
        _client(included) as second,
    ):
        async with asyncio.TaskGroup() as tasks:
            first_request = tasks.create_task(first.get("/excluded-concurrent"))
            second_request = tasks.create_task(second.get("/included-concurrent"))
        assert first_request.result().json() == "excluded-concurrent"
        assert second_request.result().json() == "included-concurrent"

    assert sorted(scenario.observations) == [
        ("excluded-concurrent", False),
        ("excluded-concurrent", False),
        ("included-concurrent", True),
        ("included-concurrent", True),
    ]
    assert _histogram_counts(reader)["http.server.request.duration"] == 1
    assert _histogram_counts(reader)["canon.port.duration"] == 2
    assert capture.one("GET /{name}").name == "GET /{name}"
    assert capture.one("GET").kind is SpanKind.CLIENT


async def test_mounted_http_application_keeps_inherited_exclusion(
    scenario: HttpScenario,
    telemetry: tuple[SpanCapture, MeterProvider, InMemoryMetricReader],
) -> None:
    capture, meter, reader = telemetry
    inner = _application(scenario.feature, capture, meter)
    outer = Composition(
        telemetry=Telemetry(
            tracer_provider=capture.provider,
            meter_provider=meter,
            logs=False,
            exclude=lambda scope: True,
        ),
    ).apply(FastAPI())
    outer.mount("/inner", inner)
    async with (
        asyncio.timeout(5),
        outer.router.lifespan_context(outer),
        inner.router.lifespan_context(inner),
        _client(outer) as client,
    ):
        assert (await client.get("/inner/included")).json() == "included"
        assert is_instrumentation_enabled()

    assert scenario.observations == [("included", False), ("included", False)]
    assert not capture.spans
    assert _histogram_counts(reader) == {}


_HTTP_CLIENT_METRICS = {"http.client.duration", "http.client.request.duration"}


def _application(
    feature: Feature, capture: SpanCapture, meter: MeterProvider
) -> FastAPI:
    return Composition(
        feature,
        telemetry=Telemetry(
            tracer_provider=capture.provider,
            meter_provider=meter,
            port_metrics=True,
            logs=False,
            exclude=lambda scope: scope["path"].startswith("/excluded"),
        ),
    ).apply(FastAPI())


def _client(app: FastAPI) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://application.test",
        timeout=5,
    )


def _histogram_counts(reader: InMemoryMetricReader) -> dict[str, int]:
    data = reader.get_metrics_data()
    if data is None:
        return {}
    return {
        metric.name: sum(point.count for point in metric.data.data_points)
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if isinstance(metric.data, Histogram)
    }
