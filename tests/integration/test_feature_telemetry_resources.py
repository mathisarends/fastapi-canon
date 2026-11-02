import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx2
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

from fastapi_canon import CanonRouter, Composition, Feature
from fastapi_canon.integrations.opentelemetry import Telemetry, instrument
from fastapi_canon.integrations.opentelemetry.httpx import instrument_httpx
from fastapi_canon.integrations.opentelemetry.sqlalchemy import instrument_sqlalchemy
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


async def test_http_request_shares_configuration_exclusions_and_resource_lifetimes() -> (
    None
):
    closed: list[str] = []

    async def downstream(request: Request) -> JSONResponse:
        return JSONResponse({"value": 1})

    class Dependencies(Provider):
        @provide(scope=Scope.APP)
        async def resources(self, telemetry: Telemetry) -> AsyncIterator[Resources]:
            engine = create_async_engine("sqlite+aiosqlite://")
            try:
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(
                        app=Starlette(routes=[Route("/", downstream)])
                    ),
                    base_url="http://downstream.test",
                ) as client:
                    with (
                        instrument_httpx(client, telemetry),
                        instrument_sqlalchemy(engine, telemetry),
                    ):
                        yield Resources(client, engine)
                closed.append("client")
            finally:
                await engine.dispose()
                closed.append("engine")

        service = provide(Service, scope=Scope.REQUEST)

    router = CanonRouter()

    @router.get("/{name}")
    @inject
    async def endpoint(name: str, service: FromDishka[Service]) -> int:
        return await service()

    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        with SpanCapture() as capture:
            app = Composition(
                Feature(
                    name="resources",
                    providers=[Dependencies],
                    routers=[router],
                    instrumentation=[instrument(Service)],
                ),
                telemetry=Telemetry(
                    tracer_provider=capture.provider,
                    meter_provider=meter,
                    port_metrics=True,
                    logs=False,
                    exclude=lambda scope: scope["path"] == "/excluded",
                ),
            ).apply(FastAPI())
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
            assert closed == ["client", "engine"]
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
