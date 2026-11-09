import asyncio

import pytest
from dishka import Provider, Scope
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fastapi_canon import Composition, Feature
from fastapi_canon.integrations.opentelemetry import Telemetry, instrument
from tests.fixtures.telemetry import SpanCapture


class Registry:
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    async def get(self) -> str:
        return self.value


class SpecializedRegistry(Registry):
    pass


class UnrelatedRegistry:
    async def get(self) -> str:
        return "unrelated"


class Token(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    registry: Registry = Field(exclude=True)


@pytest.mark.parametrize("implementation", [Registry, SpecializedRegistry])
async def test_typed_model_retains_the_instrumented_dependency(
    implementation: type[Registry],
) -> None:
    original = implementation("allowed")
    dependencies = Provider()
    dependencies.provide(lambda: original, provides=Registry, scope=Scope.APP)
    feature = Feature(
        name="registry", providers=[dependencies], instrumentation=instrument(Registry)
    )
    with SpanCapture() as capture:
        app = Composition(
            features=[feature], telemetry=Telemetry(tracer_provider=capture.provider)
        ).build(FastAPI())
        async with asyncio.timeout(5), app.router.lifespan_context(app):
            resolved = await app.state.dishka_container.get(Registry)
            token = Token(registry=resolved)
            assert token.registry is resolved
            assert resolved is not original
            assert isinstance(resolved, implementation)
            assert type(resolved) is not implementation
            assert token.model_dump() == {}
            assert await token.registry.get() == "allowed"

        span = capture.one("Registry.get")
        assert span.attributes is not None
        assert span.attributes["canon.adapter"] == implementation.__name__


async def test_typed_model_rejects_an_instrumented_unrelated_implementation() -> None:
    dependencies = Provider()
    dependencies.provide(UnrelatedRegistry, provides=Registry, scope=Scope.APP)
    feature = Feature(
        name="registry", providers=[dependencies], instrumentation=instrument(Registry)
    )
    with SpanCapture() as capture:
        app = Composition(
            features=[feature], telemetry=Telemetry(tracer_provider=capture.provider)
        ).build(FastAPI())
        async with asyncio.timeout(5), app.router.lifespan_context(app):
            resolved = await app.state.dishka_container.get(Registry)
            assert not isinstance(resolved, Registry)
            with pytest.raises(ValidationError) as error:
                Token(registry=resolved)
            assert error.value.errors()[0]["type"] == "is_instance_of"
