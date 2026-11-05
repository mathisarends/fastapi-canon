from typing import Protocol

import pytest
from dishka import Provider, Scope, provide
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_canon import APIRouter, Composition, Feature, FeatureConfigurationError


class Greeter(Protocol):
    def greet(self) -> str: ...


class RealGreeter:
    def greet(self) -> str:
        return "real"


class FakeGreeter:
    def greet(self) -> str:
        return "fake"


class GreeterProvider(Provider):
    scope = Scope.APP

    @provide
    def greeter(self) -> Greeter:
        return RealGreeter()


class FakeGreeterProvider(Provider):
    scope = Scope.APP

    @provide
    def greeter(self) -> Greeter:
        return FakeGreeter()


class UnrelatedProvider(Provider):
    scope = Scope.APP

    @provide
    def limit(self) -> int:
        return 1


def _composition() -> Composition:
    router = APIRouter(route_class=DishkaRoute)

    @router.get("/greeting")
    async def greeting(greeter: FromDishka[Greeter]) -> str:
        return greeter.greet()

    return Composition(
        features=[
            Feature(name="greetings", routers=[router], providers=[GreeterProvider])
        ]
    )


def _greeting(app: FastAPI) -> str:
    with TestClient(app) as client:
        response = client.get("/greeting")
    response.raise_for_status()
    result = response.json()
    assert isinstance(result, str)
    return result


def test_override_replaces_feature_binding_and_keeps_original() -> None:
    composition = _composition()

    overridden = composition.override(FakeGreeterProvider).build(FastAPI())
    original = composition.build(FastAPI())

    assert (_greeting(overridden), _greeting(original)) == ("fake", "real")


def test_override_accepts_provider_factories() -> None:
    app = _composition().override(lambda: FakeGreeterProvider()).build(FastAPI())

    assert _greeting(app) == "fake"


def test_override_rejects_bindings_no_feature_provides() -> None:
    composition = _composition().override(UnrelatedProvider)

    with pytest.raises(FeatureConfigurationError, match="no feature provides"):
        composition.build(FastAPI())


def test_override_requires_a_provider() -> None:
    with pytest.raises(FeatureConfigurationError, match="at least one provider"):
        _composition().override()


def test_override_rejects_duplicate_sources() -> None:
    composition = _composition().override(FakeGreeterProvider)

    with pytest.raises(FeatureConfigurationError, match="duplicates"):
        composition.override(FakeGreeterProvider)


def test_build_rejects_a_different_override_set() -> None:
    composition = _composition()
    app = composition.override(FakeGreeterProvider).build(FastAPI())

    with pytest.raises(FeatureConfigurationError, match="different composition"):
        composition.build(app)


def test_build_validates_the_installed_application() -> None:
    app = FastAPI()

    returned = _composition().build(app)

    assert returned is app
    assert _greeting(app) == "real"
