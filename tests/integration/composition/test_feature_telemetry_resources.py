import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

import httpx2
import pytest
from dishka import FromDishka, Provider, Scope, provide
from dishka.integrations.fastapi import inject
from fastapi import FastAPI
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import Histogram, InMemoryMetricReader
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastapi_canon import APIRouter, Composition, Feature
from fastapi_canon.integrations.opentelemetry import (
    ResourceInstrumentation,
    Telemetry,
    instrument,
)
from fastapi_canon.integrations.opentelemetry.httpx import (
    HttpxClients,
    instrument_httpx,
)
from tests.fixtures.telemetry import SpanCapture


@dataclass
class Resources:
    client: httpx2.AsyncClient
    engine: AsyncEngine


class Service:
    def __init__(self, resources: Resources) -> None:
        self.resources = resources

    async def __call__(self) -> int:
        async with self.resources.engine.connect() as connection:
            value = await connection.scalar(text("select 1"))
        response = await self.resources.client.get("/")
        return int(value) + int(response.json()["value"])


async def _downstream(request: Request) -> JSONResponse:
    return JSONResponse({"value": 1})


def _client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=Starlette(routes=[Route("/", _downstream)])),
        base_url="http://downstream.test",
    )


async def test_http_request_shares_configuration_exclusions_and_resource_lifetimes() -> (
    None
):
    closed: list[str] = []

    class Dependencies(Provider):
        @provide(scope=Scope.APP)
        async def client(self) -> AsyncIterator[httpx2.AsyncClient]:
            async with _client() as client:
                yield client
            closed.append("client")

        @provide(scope=Scope.APP)
        async def engine(self) -> AsyncIterator[AsyncEngine]:
            engine = create_async_engine("sqlite+aiosqlite://")
            yield engine
            await engine.dispose()
            closed.append("engine")

        resources = provide(Resources, scope=Scope.APP)
        service = provide(Service, scope=Scope.REQUEST)

    router = APIRouter()

    @router.get("/{name}")
    @inject
    async def endpoint(name: str, service: FromDishka[Service]) -> int:
        return await service()

    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        with SpanCapture() as capture:
            app = Composition(
                features=(
                    Feature(
                        name="resources",
                        providers=[Dependencies],
                        routers=[router],
                        integrations=[instrument(Service)],
                    ),
                ),
                integrations=[
                    Telemetry(
                        tracer_provider=capture.provider,
                        meter_provider=meter,
                        port_metrics=True,
                        logs=False,
                        exclude=lambda scope: scope["path"] == "/excluded",
                    )
                ],
            ).build(FastAPI())
            async with (
                asyncio.timeout(5),
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://app.test"
                ) as client,
            ):
                assert (await client.get("/excluded")).json() == 2
                assert not capture.spans
                assert (await client.get("/included")).json() == 2
            assert sorted(closed) == ["client", "engine"]
            capture.assert_tree(
                "GET /{name}",
                [("fastapi.endpoint", [("Service.__call__", ["DB query", "GET"])])],
            )
            data = reader.get_metrics_data()
            assert data is not None
            counts = {
                metric.name: sum(point.count for point in metric.data.data_points)
                for resource in data.resource_metrics
                for scope in resource.scope_metrics
                for metric in scope.metrics
                if isinstance(metric.data, Histogram)
            }
            assert (
                counts["canon.port.duration"]
                == counts["db.client.operation.duration"]
                == 1
            )
            assert (
                counts.get(
                    "http.client.duration", counts.get("http.client.request.duration")
                )
                == 1
            )
    finally:
        meter.shutdown()


@pytest.mark.parametrize(
    ("resources", "manual", "spans"),
    [
        pytest.param([], False, [], id="disabled"),
        pytest.param([HttpxClients()], True, ["GET"], id="manual-wins"),
    ],
)
async def test_resource_selection_and_manual_instrumentation(
    resources: Sequence[ResourceInstrumentation], manual: bool, spans: list[str]
) -> None:
    class Dependencies(Provider):
        @provide(scope=Scope.APP)
        async def client(
            self, telemetry: Telemetry
        ) -> AsyncIterator[httpx2.AsyncClient]:
            async with _client() as client:
                if manual:
                    with instrument_httpx(client, telemetry, capture_url=True):
                        yield client
                else:
                    yield client

    with SpanCapture() as capture:
        app = Composition(
            features=[Feature(name="resources", providers=[Dependencies])],
            integrations=[
                Telemetry(
                    tracer_provider=capture.provider,
                    metrics=False,
                    logs=False,
                    resources=resources,
                )
            ],
        ).build(FastAPI())
        async with asyncio.timeout(5), app.router.lifespan_context(app):
            client = await app.state.dishka_container.get(httpx2.AsyncClient)
            assert (await client.get("/")).json() == {"value": 1}
        assert [span.name for span in capture.spans] == spans
        # The manual context's capture_url=True wins over the automatic default.
        assert all("downstream.test" in str(span.attributes) for span in capture.spans)
