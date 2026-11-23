from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from dishka import Provider, Scope, provide
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_canon import Composition, Feature, Inject
from fastapi_canon.errors import FeatureConfigurationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.applications import Starlette as TypeOnlyApplication


class Worker:
    pass


class WorkerProvider(Provider):
    worker = provide(Worker, scope=Scope.APP)


received: list[object] = []


@asynccontextmanager
async def injected_lifespan(worker: Inject[Worker]) -> AsyncIterator[None]:
    received.append(worker)
    yield


@asynccontextmanager
async def plain_lifespan(app: TypeOnlyApplication) -> AsyncIterator[None]:
    received.append(app)
    yield


def test_type_only_return_annotation_keeps_the_injected_service() -> None:
    received.clear()
    app = Composition(
        features=[
            Feature(
                name="workers", providers=[WorkerProvider], lifespan=injected_lifespan
            )
        ]
    ).build(FastAPI())

    with TestClient(app):
        assert [type(value) for value in received] == [Worker]


def test_type_only_return_annotation_still_reports_a_missing_binding() -> None:
    composition = Composition(
        features=[Feature(name="workers", lifespan=injected_lifespan)]
    )

    with pytest.raises(FeatureConfigurationError, match=r"canon\.providers\.E005"):
        composition.build(FastAPI())


def test_unresolved_application_annotation_keeps_the_plain_contract() -> None:
    received.clear()
    app = Composition(features=[Feature(name="plain", lifespan=plain_lifespan)]).build(
        FastAPI()
    )

    with TestClient(app):
        assert received == [app]
