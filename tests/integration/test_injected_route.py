from collections.abc import AsyncIterator

import pytest
from dishka import Provider, Scope, provide
from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from fastapi_canon import APIRouter, Composition, Feature, Inject


class Store:
    def __init__(self, identifier: int) -> None:
        self.identifier = identifier


class Clock:
    def now(self) -> str:
        return "noon"


class Lifecycle:
    def __init__(self) -> None:
        self.opened: list[int] = []
        self.closed: list[int] = []

    def provider(self) -> Provider:
        lifecycle = self

        class Services(Provider):
            @provide(scope=Scope.REQUEST)
            async def store(self) -> AsyncIterator[Store]:
                identifier = len(lifecycle.opened) + 1
                lifecycle.opened.append(identifier)
                try:
                    yield Store(identifier)
                finally:
                    lifecycle.closed.append(identifier)

            @provide(scope=Scope.APP)
            def clock(self) -> Clock:
                return Clock()

        return Services()


def compose(router: APIRouter, lifecycle: Lifecycle) -> FastAPI:
    return Composition(
        features=[
            Feature(name="tasks", routers=[router], providers=[lifecycle.provider()])
        ]
    ).build(FastAPI())


def test_async_endpoints_resolve_both_markers_in_one_request_scope() -> None:
    lifecycle = Lifecycle()
    router = APIRouter(prefix="/tasks")

    @router.get("/{task_id}")
    async def read(
        task_id: int,
        store: Inject[Store],
        same_store: FromDishka[Store],
        clock: Inject[Clock],
    ) -> dict[str, object]:
        return {
            "task": task_id,
            "store": store.identifier,
            "shared": store is same_store,
            "time": clock.now(),
        }

    app = compose(router, lifecycle)
    with TestClient(app) as client:
        first = client.get("/tasks/7").json()
        second = client.get("/tasks/8").json()

    assert first == {"task": 7, "store": 1, "shared": True, "time": "noon"}
    assert second["store"] == 2
    assert lifecycle.closed == [1, 2]


def test_sync_endpoints_resolve_injected_parameters() -> None:
    lifecycle = Lifecycle()
    router = APIRouter()

    @router.get("/store")
    def read(store: Inject[Store], clock: FromDishka[Clock]) -> str:
        return f"{store.identifier} at {clock.now()}"

    app = compose(router, lifecycle)
    with TestClient(app) as client:
        assert client.get("/store").json() == "1 at noon"
    assert lifecycle.closed == [1]


def test_sse_generators_resolve_injected_parameters_without_inject() -> None:
    lifecycle = Lifecycle()
    router = APIRouter()

    @router.sse("/events")
    async def events(clock: Inject[Clock]) -> AsyncIterator[str]:
        yield clock.now()

    app = compose(router, lifecycle)
    with TestClient(app) as client:
        response = client.get("/events")

    assert 'data: "noon"' in response.text
    assert "event: canon.complete" in response.text


def test_injected_parameters_are_absent_from_the_public_contract() -> None:
    router = APIRouter()

    @router.get("/items/{item_id}")
    async def read(item_id: int, request: Request, store: Inject[Store]) -> int:
        assert request.url.path == f"/items/{item_id}"
        return store.identifier

    app = compose(router, Lifecycle())
    operation = app.openapi()["paths"]["/items/{item_id}"]["get"]
    with TestClient(app) as client:
        assert client.get("/items/3").json() == 1

    assert [parameter["name"] for parameter in operation["parameters"]] == ["item_id"]
    assert "requestBody" not in operation


def test_existing_dishka_declarations_keep_working() -> None:
    lifecycle = Lifecycle()
    decorated = APIRouter()
    explicit = APIRouter(prefix="/explicit", route_class=DishkaRoute)

    @decorated.get("/decorated")
    @inject
    async def read_decorated(store: FromDishka[Store]) -> int:
        return store.identifier

    @explicit.get("/route")
    async def read_explicit(store: FromDishka[Store]) -> int:
        return store.identifier

    app = Composition(
        features=[
            Feature(
                name="tasks",
                routers=[decorated, explicit],
                providers=[lifecycle.provider()],
            )
        ]
    ).build(FastAPI())
    with TestClient(app) as client:
        assert client.get("/decorated").json() == 1
        assert client.get("/explicit/route").json() == 2
    assert lifecycle.closed == [1, 2]


def test_injection_without_a_container_explains_the_setup() -> None:
    router = APIRouter()

    @router.get("/store")
    async def read(store: Inject[Store]) -> int:
        return store.identifier

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client, pytest.raises(RuntimeError, match="Composition"):
        client.get("/store")
