from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dishka import AsyncContainer, make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import APIRouter, FastAPI

from app.features.tasks.feature import feature as tasks_feature
from app.infrastructure.database.provider import DatabaseProvider
from app.presentation.feature import Feature
from app.presentation.health import router as health_router

FEATURES: tuple[Feature, ...] = (tasks_feature,)


def create_container() -> AsyncContainer:
    feature_providers = [
        provider() for feature in FEATURES for provider in feature.providers
    ]
    return make_async_container(DatabaseProvider(), *feature_providers)


def register_features(app: FastAPI) -> None:
    app.include_router(health_router)
    api = APIRouter(prefix="/api/v1")
    for feature in FEATURES:
        for router in feature.routers:
            api.include_router(router)
        if feature.register_exception_handlers is not None:
            feature.register_exception_handlers(app)
    app.include_router(api)


def create_app() -> FastAPI:
    container = create_container()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        await container.close()

    app = FastAPI(title="FastAPI Canon", lifespan=lifespan)
    register_features(app)
    setup_dishka(container, app)
    return app


app = create_app()
