from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from dishka import Provider, Scope, provide
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_canon import Composition, Feature, Inject
from fastapi_canon.errors import FeatureConfigurationError


class Worker:
    def __init__(self) -> None:
        self.events: list[str] = []


class WorkerProvider(Provider):
    worker = provide(Worker, scope=Scope.APP)


class RequestWorkerProvider(Provider):
    worker = provide(Worker, scope=Scope.REQUEST)


@asynccontextmanager
async def worker_lifespan(app: FastAPI, worker: Inject[Worker]) -> AsyncIterator[None]:
    app.state.worker = worker
    worker.events.append("start")
    try:
        yield
    finally:
        worker.events.append("stop")


def test_lifespan_receives_injected_app_services_and_the_application() -> None:
    app = Composition(
        features=(
            Feature(
                name="workers", providers=[WorkerProvider], lifespan=worker_lifespan
            ),
        )
    ).build(FastAPI())

    with TestClient(app):
        worker = app.state.worker
        assert worker.events == ["start"]

    assert worker.events == ["start", "stop"]


def test_lifespan_service_without_app_scope_binding_fails_preflight() -> None:
    composition = Composition(
        features=(
            Feature(
                name="workers",
                providers=[RequestWorkerProvider],
                lifespan=worker_lifespan,
            ),
        )
    )

    report = composition.check(FastAPI())

    issue = next(
        issue for issue in report.issues if issue.code == "canon.providers.E005"
    )
    assert issue.feature_names == ("workers",)
    assert issue.locations[-1].endswith(".Worker")
    with pytest.raises(FeatureConfigurationError, match=r"canon\.providers\.E005"):
        composition.build(FastAPI())


def test_lifespan_rejects_parameters_that_are_neither_app_nor_injected() -> None:
    @asynccontextmanager
    async def ambiguous(
        app: FastAPI, name: str, worker: Inject[Worker]
    ) -> AsyncIterator[None]:
        del app, name, worker
        yield

    with pytest.raises(FeatureConfigurationError, match="app, name are neither"):
        Feature(name="workers", lifespan=ambiguous)
