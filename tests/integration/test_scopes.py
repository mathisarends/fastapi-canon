import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from dishka import FromDishka, Provider, Scope, make_async_container, provide
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_canon import Composition, Feature, Scoped


@dataclass
class Resource:
    closed: bool = False


async def test_scoped_calls_have_independent_resources_and_close_before_return() -> (
    None
):
    resources: list[Resource] = []
    entered = 0
    both_entered = asyncio.Event()

    class Resources(Provider):
        @provide(scope=Scope.REQUEST)
        async def resource(self) -> AsyncIterator[Resource]:
            resource = Resource()
            resources.append(resource)
            try:
                yield resource
            finally:
                resource.closed = True

    class UseCase:
        def __init__(self, resource: Resource) -> None:
            self.resource = resource

        async def __call__(self, value: int) -> int:
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await both_entered.wait()
            assert not self.resource.closed
            return value + 1

    provider = Resources()
    provider.provide(UseCase, scope=Scope.REQUEST)
    async with asyncio.timeout(5), make_async_container(provider) as container:
        scoped = Scoped(container, UseCase)
        assert await asyncio.gather(scoped(1), scoped(2)) == [2, 3]
        assert len(resources) == 2
        assert resources[0] is not resources[1]
        assert resources[0].closed and resources[1].closed
        # Reusing the wrapper also proves it did not close its parent container.
        assert await scoped(3) == 4
        assert len(resources) == 3 and resources[2].closed


@pytest.mark.parametrize("outcome", ["error", "cancel", "cleanup-error"])
async def test_scoped_unwinds_and_propagates_failures(outcome: str) -> None:
    resources: list[Resource] = []
    started = asyncio.Event()

    class Resources(Provider):
        @provide(scope=Scope.REQUEST)
        async def resource(self) -> AsyncIterator[Resource]:
            resource = Resource()
            resources.append(resource)
            try:
                yield resource
            finally:
                resource.closed = True
                if outcome == "cleanup-error":
                    raise ValueError("cleanup failed")

    class UseCase:
        def __init__(self, resource: Resource) -> None:
            self.resource = resource

        async def __call__(self) -> int:
            started.set()
            if outcome == "cancel":
                await asyncio.Event().wait()
            if outcome == "error":
                raise ValueError("use case failed")
            return 1

    provider = Resources()
    provider.provide(UseCase, scope=Scope.REQUEST)
    async with asyncio.timeout(5), make_async_container(provider) as container:
        task = asyncio.create_task(Scoped(container, UseCase)())
        await started.wait()
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises((ValueError, ExceptionGroup)):
                await task
        assert len(resources) == 1 and resources[0].closed


async def test_routes_declared_before_build_share_the_request_scope() -> None:
    # Direct and already included routes are re-wired by composition. This
    # covers FastAPI's lazily included routers and runs against new releases.
    resources: list[Resource] = []
    calls: list[str] = []

    class Resources(Provider):
        @provide(scope=Scope.REQUEST)
        async def resource(self) -> AsyncIterator[Resource]:
            resource = Resource()
            resources.append(resource)
            try:
                yield resource
            finally:
                resource.closed = True

    async def prerequisite() -> None:
        calls.append("dependency")

    app = FastAPI(dependencies=[Depends(prerequisite, use_cache=False)])
    included = APIRouter(prefix="/included")

    @app.get("/direct")
    @inject
    async def direct(resource: FromDishka[Resource]) -> bool:
        return resource.closed

    @included.get("/nested")
    @inject
    async def nested(resource: FromDishka[Resource]) -> bool:
        return resource.closed

    app.include_router(included)
    Composition(features=[Feature(name="resources", providers=[Resources()])]).build(
        app
    )

    async with (
        asyncio.timeout(5),
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
    ):
        assert (await client.get("/direct")).json() is False
        assert (await client.get("/included/nested")).json() is False
    assert len(resources) == 2
    assert all(resource.closed for resource in resources)
    assert calls == ["dependency", "dependency"]
