import asyncio
from collections.abc import AsyncIterator

import pytest
from dishka import Provider, Scope, make_async_container, provide
from fastapi import FastAPI

from fastapi_canon import Composition, Feature
from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry import Telemetry
from tests.fixtures.telemetry import SpanCapture


class Resource:
    def __init__(self, telemetry: Telemetry) -> None:
        self.telemetry = telemetry


def test_composition_binds_borrowed_configuration_per_app_and_component() -> None:
    closed: list[Telemetry] = []

    class Resources(Provider):
        @provide(scope=Scope.APP)
        async def resource(self, telemetry: Telemetry) -> AsyncIterator[Resource]:
            yield Resource(telemetry)
            closed.append(telemetry)

    shared = Feature(
        name="resources", providers=[Resources(), Resources(component="billing")]
    )
    with SpanCapture() as first, SpanCapture() as second:
        first_options = Telemetry(tracer_provider=first.provider, logs=False)
        second_options = Telemetry(
            tracer_provider=second.provider, tracing=False, logs=False
        )
        first_app = Composition(features=(shared,), telemetry=first_options).build(
            FastAPI()
        )
        second_app = Composition(features=(shared,), telemetry=second_options).build(
            FastAPI()
        )
        disabled = Composition(features=(shared,)).build(FastAPI())

        async def run() -> None:
            async with (
                asyncio.timeout(5),
                first_app.router.lifespan_context(first_app),
                second_app.router.lifespan_context(second_app),
                disabled.router.lifespan_context(disabled),
            ):
                one = await first_app.state.dishka_container.get(Resource)
                named = await first_app.state.dishka_container.get(
                    Resource, component="billing"
                )
                two = await second_app.state.dishka_container.get(Resource)
                off = await disabled.state.dishka_container.get(Resource)
                assert one.telemetry is named.telemetry is first_options
                assert two.telemetry is second_options
                assert off.telemetry == Telemetry.disabled()
                assert not off.telemetry.tracing and not off.telemetry.metrics
                assert one is await first_app.state.dishka_container.get(Resource)

        asyncio.run(run())
        assert len(closed) == 4
        with first.provider.get_tracer("owner").start_as_current_span("still-owned"):
            pass
        assert first.one("still-owned").name == "still-owned"
        assert not second.spans


def test_standalone_dishka_configuration_is_explicit_and_reusable() -> None:
    dependencies = Provider()
    dependencies.provide(Resource, scope=Scope.APP)
    options = Telemetry.disabled()

    async def run() -> tuple[Resource, Telemetry]:
        container = make_async_container(dependencies, options.provider())
        try:
            async with asyncio.timeout(5):
                assert (await container.get(Resource)).telemetry is options
                return await container.get(Resource), await container.get(Telemetry)
        finally:
            await container.close()

    resource, resolved = asyncio.run(run())
    assert resource.telemetry is resolved is options


def test_composition_rejects_a_second_telemetry_binding() -> None:
    with pytest.raises(FeatureConfigurationError, match="Telemetry is reserved"):
        Composition(
            features=(
                Feature(name="duplicate", providers=[Telemetry.disabled().provider()]),
            )
        ).build(FastAPI())
