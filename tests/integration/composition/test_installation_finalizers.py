from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from dishka import Provider, Scope
from fastapi import FastAPI

from fastapi_canon import Composition, Feature
from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry import Telemetry


class Resource:
    pass


def _telemetry(events: list[str]) -> Telemetry:
    return replace(Telemetry.disabled(), shutdown=lambda: events.append("shutdown"))


def _resource_provider(events: list[str]) -> Provider:
    async def resource() -> AsyncIterator[Resource]:
        yield Resource()
        events.append("container")

    provider = Provider(scope=Scope.APP)
    provider.provide(resource)
    return provider


async def test_shutdown_runs_once_after_lifespans_and_container_close() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        events.append("feature")

    app = Composition(
        features=[
            Feature(
                name="work",
                providers=[_resource_provider(events)],
                lifespan=lifespan,
            )
        ],
        integrations=[_telemetry(events)],
    ).build(FastAPI())

    async with app.router.lifespan_context(app):
        await app.state.dishka_container.get(Resource)

    assert events == ["feature", "container", "shutdown"]


async def test_shutdown_runs_when_a_feature_lifespan_fails_during_startup() -> None:
    events: list[str] = []

    def fail_startup() -> None:
        raise RuntimeError("startup failed")

    @asynccontextmanager
    async def failing(_app: FastAPI) -> AsyncIterator[None]:
        fail_startup()
        yield

    app = Composition(
        features=[
            Feature(
                name="work", providers=[_resource_provider(events)], lifespan=failing
            )
        ],
        integrations=[_telemetry(events)],
    ).build(FastAPI())

    with pytest.raises(RuntimeError, match="startup failed"):
        async with app.router.lifespan_context(app):
            pass

    assert events == ["shutdown"]


def test_shutdown_runs_before_a_build_error_propagates() -> None:
    events: list[str] = []
    provider = _resource_provider(events)
    composition = Composition(
        features=[
            Feature(name="first", providers=[provider]),
            Feature(name="second", providers=[lambda: provider]),
        ],
        integrations=[_telemetry(events)],
    )

    with pytest.raises(FeatureConfigurationError):
        composition.build(FastAPI())

    assert events == ["shutdown"]


def test_check_and_a_never_started_app_do_not_shut_down() -> None:
    events: list[str] = []
    composition = Composition(integrations=[_telemetry(events)])

    composition.check(FastAPI())
    composition.build(FastAPI())

    assert events == []


def test_failed_rebuild_keeps_the_installed_application_running() -> None:
    events: list[str] = []
    telemetry = _telemetry(events)
    app = Composition(integrations=[telemetry]).build(FastAPI())

    with pytest.raises(FeatureConfigurationError, match="different composition"):
        Composition(integrations=[telemetry], request_logging=True).build(app)

    assert events == []


def test_shutdown_must_be_callable() -> None:
    with pytest.raises(FeatureConfigurationError, match="shutdown must be callable"):
        Telemetry(shutdown="close")  # type: ignore[arg-type]  # Invalid on purpose.
