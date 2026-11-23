from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from dishka import AsyncContainer, Provider, Scope, provide
from dishka.exceptions import NoFactoryError
from fastapi import FastAPI

from fastapi_canon import Composition, Feature, ServiceScope
from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integration import Integration, ProviderContext
from fastapi_canon.integrations.opentelemetry import Telemetry


@dataclass(frozen=True)
class Greeting:
    text: str


class Resource:
    pass


class GreetingProvider(Provider):
    scope = Scope.APP

    @provide
    def greeting(self) -> Greeting:
        return Greeting("real")


class FakeGreetingProvider(Provider):
    scope = Scope.APP

    @provide
    def greeting(self) -> Greeting:
        return Greeting("fake")


class GreetingIntegration(Integration):
    def providers(self, context: ProviderContext) -> Sequence[Provider]:
        return (GreetingProvider(),)


class ScopedGreetingProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def greeting(self) -> Greeting:
        return Greeting("scoped")

    @provide(scope=Scope.APP)
    def greeting_scope(self, container: AsyncContainer) -> ServiceScope[Greeting]:
        return ServiceScope(container, Greeting)


def _finalized_resource(events: list[str]) -> Provider:
    async def resource() -> AsyncIterator[Resource]:
        yield Resource()
        events.append("finalized")

    provider = Provider(scope=Scope.APP)
    provider.provide(resource)
    return provider


async def test_container_resolves_feature_and_integration_bindings() -> None:
    telemetry = Telemetry.disabled()
    composition = Composition(
        features=[Feature(name="greetings", providers=[GreetingProvider])],
        integrations=[telemetry],
    )

    async with composition.container() as container:
        greeting = await container.get(Greeting)
        configured = await container.get(Telemetry)

    assert greeting.text == "real"
    assert configured is telemetry


async def test_container_applies_overrides() -> None:
    composition = Composition(
        features=[Feature(name="greetings", providers=[GreetingProvider])]
    ).override(FakeGreetingProvider)

    async with composition.container() as container:
        greeting = await container.get(Greeting)

    assert greeting.text == "fake"


async def test_container_finalizes_app_resources_also_when_the_body_raises() -> None:
    events: list[str] = []
    composition = Composition(
        features=[Feature(name="resources", providers=[_finalized_resource(events)])]
    )

    async with composition.container() as container:
        await container.get(Resource)
    assert events == ["finalized"]

    with pytest.raises(RuntimeError, match="body failed"):
        async with composition.container() as container:
            await container.get(Resource)
            raise RuntimeError("body failed")
    assert events == ["finalized", "finalized"]


async def test_invalid_graph_fails_with_the_build_error() -> None:
    composition = Composition(
        features=[Feature(name="greetings", providers=[FakeGreetingProvider])],
        integrations=[GreetingIntegration()],
    )
    with pytest.raises(FeatureConfigurationError) as built:
        composition.build(FastAPI())

    with pytest.raises(FeatureConfigurationError) as opened:
        async with composition.container():
            pass

    assert str(opened.value) == str(built.value)
    assert "canon.providers.E006" in str(opened.value)


async def test_composition_builds_its_own_container_after_a_standalone_one() -> None:
    events: list[str] = []
    composition = Composition(
        features=[Feature(name="resources", providers=[_finalized_resource(events)])]
    )

    async with composition.container() as container:
        standalone = await container.get(Resource)
    app = composition.build(FastAPI())
    async with app.router.lifespan_context(app):
        installed = await app.state.dishka_container.get(Resource)

    assert installed is not standalone
    assert events == ["finalized", "finalized"]


async def test_container_does_not_enter_feature_lifespans() -> None:
    entered: list[str] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        entered.append("feature")
        yield

    composition = Composition(
        features=[Feature(name="work", providers=[GreetingProvider], lifespan=lifespan)]
    )

    async with composition.container() as container:
        await container.get(Greeting)

    assert entered == []


async def test_composition_without_providers_yields_an_empty_container() -> None:
    async with Composition().container() as container:
        with pytest.raises(NoFactoryError):
            await container.get(Greeting)


async def test_service_scope_works_with_the_standalone_container() -> None:
    composition = Composition(
        features=[Feature(name="greetings", providers=[ScopedGreetingProvider])]
    )

    async with composition.container() as container:
        scope = await container.get(ServiceScope[Greeting])
        async with scope() as greeting:
            text = greeting.text

    assert text == "scoped"
