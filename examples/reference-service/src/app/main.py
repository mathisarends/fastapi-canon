from __future__ import annotations

from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, ErrorOptions, Feature
from fastapi_canon.integrations.opentelemetry import Telemetry

from app.authentication.feature import feature as authentication_feature
from app.features.tasks.feature import feature as tasks_feature
from app.infrastructure.database.provider import DatabaseProvider
from app.presentation.health import router as health_router

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
        # The tasks feature instruments its ports, so Telemetry is always enabled.
        integrations=[telemetry if telemetry is not None else Telemetry.disabled()],
    )
    return composition.build(app)


app = create_app()
