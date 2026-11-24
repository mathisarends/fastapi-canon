from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import httpx
import pytest
from dishka import FromDishka, Provider, Scope, provide
from dishka.integrations.fastapi import inject
from fastapi import FastAPI
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from fastapi_canon import APIRouter, Composition, Feature, export_contracts
from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry import (
    SdkProviders,
    Telemetry,
    instrument,
)


class RecordingSdk:
    """An SDK factory that gives every entry its own in-memory exporter."""

    def __init__(self) -> None:
        self.exporters: list[InMemorySpanExporter] = []
        self.events: list[str] = []

    @contextmanager
    def __call__(self) -> Iterator[SdkProviders]:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        self.exporters.append(exporter)
        self.events.append("enter")
        try:
            yield SdkProviders(tracer_provider=provider)
        finally:
            provider.shutdown()
            self.events.append("exit")

    def spans(self, entry: int = 0) -> tuple[ReadableSpan, ...]:
        return self.exporters[entry].get_finished_spans()

    def names(self, entry: int = 0) -> list[str]:
        return [span.name for span in self.spans(entry)]


class Audit:
    async def record(self) -> None:
        pass


class Connection:
    pass


class OrderRepository:
    async def get(self) -> str:
        return "order"


class BillingGateway:
    async def charge(self) -> None:
        pass


class Dependencies(Provider):
    audit = provide(Audit, scope=Scope.APP)
    repository = provide(OrderRepository, scope=Scope.REQUEST)

    @provide(scope=Scope.APP)
    async def connection(self, audit: Audit) -> AsyncIterator[Connection]:
        yield Connection()
        await audit.record()


def orders(**options: Any) -> Feature:
    router = APIRouter()

    @router.get("/orders")
    @inject
    async def get_order(repository: FromDishka[OrderRepository]) -> str:
        return await repository.get()

    return Feature(
        name="orders",
        routers=[router],
        providers=[Dependencies],
        integrations=[instrument(OrderRepository, Audit)],
        **options,
    )


def health() -> Feature:
    router = APIRouter()

    @router.get("/orders")
    async def get_order() -> str:
        return "ok"

    return Feature(name="health", routers=[router])


def build(sdk: RecordingSdk, *features: Feature) -> FastAPI:
    return Composition(
        features=features or [orders()], integrations=[Telemetry(sdk=sdk)]
    ).build(FastAPI())


async def get_orders(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/orders")
    assert response.status_code == 200


async def test_port_span_joins_the_request_trace() -> None:
    sdk = RecordingSdk()
    app = build(sdk)

    async with app.router.lifespan_context(app):
        await get_orders(app)

    spans = {span.name: span for span in sdk.spans()}
    server, port = spans["GET /orders"], spans["OrderRepository.get"]
    assert port.context is not None and server.context is not None
    assert port.context.trace_id == server.context.trace_id
    assert sdk.events == ["enter", "exit"]


async def test_app_without_providers_records_http_spans() -> None:
    sdk = RecordingSdk()
    app = build(sdk, health())

    async with app.router.lifespan_context(app):
        await get_orders(app)

    assert "GET /orders" in sdk.names()


async def test_each_app_owns_its_sdk_providers() -> None:
    sdk = RecordingSdk()
    telemetry = Telemetry(sdk=sdk)
    outer, inner = (
        Composition(features=[orders()], integrations=[telemetry]).build(FastAPI())
        for _ in range(2)
    )

    async with outer.router.lifespan_context(outer):
        async with inner.router.lifespan_context(inner):
            pass
        await get_orders(outer)
        assert sdk.events == ["enter", "enter", "exit"]

    assert "GET /orders" in sdk.names(0)


async def test_sdk_closes_after_container_finalizers() -> None:
    sdk = RecordingSdk()
    app = build(sdk)

    async with app.router.lifespan_context(app):
        await app.state.dishka_container.get(Connection)

    assert sdk.names() == ["Audit.record"]
    assert sdk.events == ["enter", "exit"]


async def test_components_share_one_sdk() -> None:
    sdk = RecordingSdk()
    billing = Provider(scope=Scope.APP, component="billing")
    billing.provide(BillingGateway)
    feature = Feature(
        name="billing",
        providers=[billing],
        integrations=[instrument(BillingGateway, component="billing")],
    )
    app = build(sdk, orders(), feature)

    async with app.router.lifespan_context(app):
        gateway = await app.state.dishka_container.get(
            BillingGateway, component="billing"
        )
        await gateway.charge()

    assert sdk.names() == ["BillingGateway.charge"]
    assert sdk.events == ["enter", "exit"]


async def test_failed_startup_exits_the_sdk() -> None:
    def fail() -> None:
        raise RuntimeError("startup failed")

    @asynccontextmanager
    async def failing(_app: FastAPI) -> AsyncIterator[None]:
        fail()
        yield

    sdk = RecordingSdk()
    app = build(sdk, orders(lifespan=failing))

    with pytest.raises(RuntimeError, match="startup failed"):
        async with app.router.lifespan_context(app):
            pass

    assert sdk.events == ["enter", "exit"]


def test_build_check_and_contract_export_do_not_enter_the_sdk() -> None:
    sdk = RecordingSdk()
    composition = Composition(features=[orders()], integrations=[Telemetry(sdk=sdk)])
    app = composition.build(FastAPI())

    assert not composition.check(app).has_errors
    assert export_contracts(app)

    assert sdk.events == []


async def test_standalone_container_owns_the_sdk() -> None:
    sdk = RecordingSdk()
    composition = Composition(features=[orders()], integrations=[Telemetry(sdk=sdk)])

    async with composition.container() as container:
        telemetry = await container.get(Telemetry)
        assert isinstance(telemetry.tracer_provider, TracerProvider)

    assert sdk.events == ["enter", "exit"]


async def test_sdk_must_yield_sdk_providers() -> None:
    @contextmanager
    def wrong() -> Iterator[Any]:
        yield TracerProvider()

    app = Composition(features=[orders()], integrations=[Telemetry(sdk=wrong)]).build(
        FastAPI()
    )

    with pytest.raises(FeatureConfigurationError, match="must yield SdkProviders"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.parametrize(
    "options",
    [
        {"tracer_provider": TracerProvider()},
        {"shutdown": lambda: None},
    ],
    ids=["borrowed-provider", "shutdown"],
)
def test_sdk_excludes_borrowed_providers(options: dict[str, Any]) -> None:
    with pytest.raises(FeatureConfigurationError, match="sdk owns the providers"):
        Telemetry(sdk=RecordingSdk(), **options)


def test_sdk_must_be_callable() -> None:
    with pytest.raises(FeatureConfigurationError, match="sdk must be callable"):
        Telemetry(sdk="console")  # type: ignore[arg-type]  # Invalid on purpose.


def test_sdk_does_not_configure_websocket_middleware() -> None:
    with pytest.raises(FeatureConfigurationError, match="explicit providers"):
        Telemetry(sdk=RecordingSdk()).websocket_middleware()
