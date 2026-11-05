from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, ErrorOptions, Feature

from app.authentication.feature import feature as authentication_feature
from app.features.tasks.feature import feature as tasks_feature
from app.infrastructure.database.provider import DatabaseProvider
from app.presentation.health import router as health_router

if TYPE_CHECKING:
    from fastapi_canon.integrations.opentelemetry import Telemetry

FEATURES: tuple[Feature, ...] = (
    Feature(name="database", providers=[DatabaseProvider]),
    authentication_feature,
    tasks_feature,
)


# Public ASGI application factory used by deployment and tests.
def create_app(  # smelt: ignore[SMT429] -- external API
    *, telemetry: Telemetry | None = None
) -> FastAPI:
    app = FastAPI(title="FastAPI Canon reference service")
    app.include_router(health_router)
    composition = Composition(
        features=FEATURES,
        router_factory=lambda: APIRouter(prefix="/api/v1"),
        errors=ErrorOptions(type_base="https://api.example.test/problems"),
        telemetry=telemetry,
    )
    return composition.build(app)


app = create_app()
