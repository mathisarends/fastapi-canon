"""Opt-in, application-owned OTLP traces and logs for the two-service demo."""

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from dishka import Provider, Scope, provide
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, ErrorOptions, Feature, Inject
from fastapi_canon.integrations.opentelemetry import Telemetry, instrument
from fastapi_canon.integrations.opentelemetry.httpx import instrument_httpx
from fastapi_canon.integrations.opentelemetry.sqlalchemy import instrument_sqlalchemy
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from starlette.types import Lifespan

from app.infrastructure.database.provider import DatabaseProvider
from app.main import FEATURES
from app.presentation.health import router as health_router
from app.settings import DatabaseSettings
from app.telemetry_logging import export_python_logs

logger = logging.getLogger(__name__)


def _telemetry(name: str) -> tuple[Telemetry, Lifespan[FastAPI]]:
    resource = Resource.create({"service.name": name})
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(1.0)),
    )
    logs = LoggerProvider(resource=resource)
    logs.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(
                endpoint=os.getenv(
                    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
                    "http://localhost:4318/v1/logs",
                )
            )
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=os.getenv(
                    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                    "http://localhost:4318/v1/traces",
                ),
            )
        )
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with export_python_logs(logs):
            try:
                yield
            finally:
                provider.shutdown()
                logs.shutdown()

    return (
        Telemetry(
            tracer_provider=provider,
            metrics=False,
            logs=False,
            exclude=lambda scope: scope["path"] in {"/health", "/ready"},
        ),
        lifespan,
    )


class NotificationGateway(ABC):
    @abstractmethod
    async def send(self) -> None: ...


class HttpNotificationGateway(NotificationGateway):
    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def send(self) -> None:
        response = await self.client.post("/notifications")
        response.raise_for_status()


def _notifier_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=os.getenv("NOTIFIER_URL", "http://localhost:8001")
    )


# Public demo application factory for standalone telemetry services.
def create_tasks_app() -> FastAPI:  # smelt: ignore[SMT429] -- external API
    options, lifecycle = _telemetry("tasks")

    class TracedDatabaseProvider(DatabaseProvider):
        @provide(scope=Scope.APP, override=True)
        async def traced_engine(
            self, settings: DatabaseSettings, telemetry: Telemetry
        ) -> AsyncIterator[AsyncEngine]:
            engine = create_async_engine(settings.url, pool_pre_ping=True)
            try:
                with instrument_sqlalchemy(engine, telemetry, pool_name="tasks"):
                    yield engine
            finally:
                await engine.dispose()

    app = FastAPI(title="Canon telemetry: tasks", lifespan=lifecycle)
    app.include_router(health_router)
    Composition(
        features=(
            Feature(name="database", providers=(TracedDatabaseProvider,)),
            *FEATURES[1:],
            _notification_feature(),
        ),
        integrations=[options],
        request_logging=True,
        errors=ErrorOptions(type_base="https://api.example.test/problems"),
        router_factory=lambda: APIRouter(prefix="/api/v1"),
    ).build(app)
    return app


# Public demo application factory for standalone telemetry services.
def create_notifier_app() -> FastAPI:  # smelt: ignore[SMT429] -- external API
    options, lifecycle = _telemetry("notifier")
    router = APIRouter()

    @router.post("/notifications")
    async def notify() -> dict[str, str]:
        logger.info("Notification received")
        return {"status": "received"}

    return Composition(
        features=(Feature(name="notifications", routers=(router,)),),
        integrations=[options],
        request_logging=True,
    ).build(FastAPI(title="Canon telemetry: notifier", lifespan=lifecycle))


def _notification_feature() -> Feature:
    class NotifierProvider(Provider):
        @provide(scope=Scope.APP)
        async def client(
            self, telemetry: Telemetry
        ) -> AsyncIterator[httpx2.AsyncClient]:
            async with _notifier_client() as client:
                with instrument_httpx(client, telemetry):
                    yield client

        gateway = provide(
            HttpNotificationGateway, provides=NotificationGateway, scope=Scope.REQUEST
        )

    router = APIRouter()

    @router.post("/demo/notify")
    async def notify(gateway: Inject[NotificationGateway]) -> dict[str, str]:
        logger.info("Sending notification")
        await gateway.send()
        return {"status": "sent"}

    return Feature(
        name="notifications",
        routers=(router,),
        providers=(NotifierProvider,),
        integrations=[instrument(NotificationGateway)],
    )
