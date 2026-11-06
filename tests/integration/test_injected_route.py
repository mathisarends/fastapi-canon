import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject, setup_dishka
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from fastapi_canon import (
    APIRouter,
    Composition,
    Feature,
    Inject,
)
from fastapi_canon.exceptions import FeatureConfigurationError


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


def register_reader(router: APIRouter, kind: str) -> None:
    match kind:
        case "async":

            @router.get("/{item_id}")
            async def read(
                item_id: int, store: Inject[Store], same: FromDishka[Store]
            ) -> str:
                return f"{item_id}:{store.identifier}:{store is same}"
        case "sync":

            @router.get("/{item_id}")
            def read(
                item_id: int, store: Inject[Store], same: FromDishka[Store]
            ) -> str:
                return f"{item_id}:{store.identifier}:{store is same}"
        case "sse-async":

            @router.sse("/{item_id}")
            async def read(
                item_id: int, store: Inject[Store], same: FromDishka[Store]
            ) -> AsyncIterator[str]:
                yield f"{item_id}:{store.identifier}:{store is same}"
        case "sse-sync":

            @router.sse("/{item_id}")
            def read(
                item_id: int, store: Inject[Store], same: FromDishka[Store]
            ) -> Iterator[str]:
                yield f"{item_id}:{store.identifier}:{store is same}"
        case _:
            raise ValueError(kind)


@pytest.mark.parametrize("kind", ["async", "sync", "sse-async", "sse-sync"])
@pytest.mark.parametrize(
    "unrelated", [False, True], ids=["no-container", "unrelated-graph"]
)
def test_missing_endpoint_bindings_reject_before_installation(
    kind: str, unrelated: bool
) -> None:
    events: list[str] = []

    class Other(Provider):
        @provide(scope=Scope.APP)
        def clock(self) -> Clock:
            events.append("constructed")
            return Clock()

    router = APIRouter(prefix="/items")
    register_reader(router, kind)
    composition = Composition(
        features=[
            Feature(
                name="items", routers=[router], providers=[Other()] if unrelated else []
            )
        ],
        router_factory=lambda: APIRouter(prefix="/api"),
    )
    app = FastAPI()
    routes = tuple(app.routes)
    lifespan = app.router.lifespan_context

    [issue] = composition.check(app).issues

    assert issue.code == "canon.providers.E003"
    assert issue.feature_names == ("items",)
    assert issue.locations == (
        "GET /api/items/{item_id}",
        f"{Store.__module__}.{Store.__qualname__}",
    )
    with pytest.raises(FeatureConfigurationError, match=r"canon\.providers\.E003"):
        composition.build(app)
    assert events == []
    assert tuple(app.routes) == routes
    assert app.router.lifespan_context is lifespan
    assert app.openapi_schema is None
    assert not hasattr(app.state, "dishka_container")


@pytest.mark.parametrize(
    ("kind", "scope"),
    [
        ("async", Scope.APP),
        ("async", Scope.REQUEST),
        ("sync", Scope.APP),
        ("sync", Scope.REQUEST),
        ("sse-async", Scope.APP),
        ("sse-sync", Scope.APP),
    ],
)
def test_bound_http_and_stream_roots_preserve_schemas_and_cleanup(
    kind: str, scope: Scope
) -> None:
    events: list[str] = []

    class Services(Provider):
        @provide(scope=scope)
        async def store(self) -> AsyncIterator[Store]:
            events.append("open")
            try:
                yield Store(1)
            finally:
                events.append("close")

    router = APIRouter(prefix="/items")
    register_reader(router, kind)
    composition = Composition(
        features=[Feature(name="items", routers=[router], providers=[Services()])]
    )
    app = FastAPI()
    report = composition.check(app)
    assert report.complete and not report.has_errors
    composition.build(app)
    assert events == []
    operation = app.openapi()["paths"]["/items/{item_id}"]["get"]
    assert [item["name"] for item in operation["parameters"]] == ["item_id"]
    assert "requestBody" not in operation

    async def scenario() -> None:
        async with (
            asyncio.timeout(5),
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app), base_url="http://test", timeout=5
            ) as client,
        ):
            response = await client.get("/items/7")
            assert response.status_code == 200
            assert "7:1:True" in response.text
            assert "canon.problem" not in response.text
            if kind.startswith("sse"):
                assert "canon.complete" in response.text
            assert events == (["open", "close"] if scope is Scope.REQUEST else ["open"])

    asyncio.run(scenario())
    assert events == ["open", "close"]


@pytest.mark.parametrize("kind", ["sse-async", "sse-sync"])
def test_stream_bodies_cannot_borrow_the_closed_http_request_scope(kind: str) -> None:
    lifecycle = Lifecycle()
    router = APIRouter()
    register_reader(router, kind)
    composition = Composition(
        features=[
            Feature(name="items", routers=[router], providers=[lifecycle.provider()])
        ]
    )

    [issue] = composition.check(FastAPI()).issues

    assert issue.code == "canon.providers.E003"
    assert "APP scope" in issue.message
    assert "ServiceScope" in issue.hint
    assert lifecycle.opened == lifecycle.closed == []


def test_fastapi_dependencies_are_not_dishka_roots() -> None:
    calls: list[str] = []

    def value() -> int:
        calls.append("dependency")
        return 9

    router = APIRouter()

    @router.get("/read")
    async def read(item: int = Depends(value)) -> int:
        return item

    composition = Composition(features=[Feature(name="items", routers=[router])])
    app = FastAPI()
    assert not composition.check(app).has_errors
    composition.build(app)
    assert calls == []

    async def scenario() -> None:
        async with (
            asyncio.timeout(5),
            AsyncClient(
                transport=ASGITransport(app), base_url="http://test", timeout=5
            ) as client,
        ):
            assert (await client.get("/read")).json() == 9

    asyncio.run(scenario())
    assert calls == ["dependency"]


def test_predecorated_async_dishka_roots_are_checked() -> None:
    router = APIRouter()

    @router.get("/read")
    @inject
    async def read(store: FromDishka[Store]) -> int:
        return store.identifier

    composition = Composition(features=[Feature(name="items", routers=[router])])

    [issue] = composition.check(FastAPI()).issues

    assert issue.code == "canon.providers.E003"
    assert "Store" in issue.message


def test_custom_route_classes_retain_dependency_ownership() -> None:
    class CustomRoute(DishkaRoute):
        pass

    lifecycle = Lifecycle()
    router = APIRouter(route_class=CustomRoute)

    @router.get("/read")
    async def read(store: FromDishka[Store]) -> int:
        return store.identifier

    composition = Composition(
        features=[
            Feature(name="items", routers=[router], providers=[lifecycle.provider()])
        ]
    )
    app = FastAPI()
    report = composition.check(app)
    assert not report.has_errors
    assert any(
        check.name.startswith("http.endpoint_dependencies:")
        and check.status == "skipped"
        for check in report.checks
    )
    composition.build(app)

    async def scenario() -> None:
        async with (
            asyncio.timeout(5),
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app), base_url="http://test", timeout=5
            ) as client,
        ):
            assert (await client.get("/read")).json() == 1

    asyncio.run(scenario())
    assert lifecycle.closed == [1]


def test_external_containers_are_not_replaced_or_resolved_by_preflight() -> None:
    lifecycle = Lifecycle()
    container = make_async_container(lifecycle.provider())
    app = FastAPI()
    setup_dishka(container, app)
    router = APIRouter()

    @router.get("/read")
    async def read(store: Inject[Store]) -> int:
        return store.identifier

    composition = Composition(features=[Feature(name="items", routers=[router])])
    report = composition.check(app)
    assert not report.has_errors
    assert any(
        check.name.startswith("http.endpoint_dependencies:")
        and check.status == "skipped"
        for check in report.checks
    )
    composition.build(app)
    assert lifecycle.opened == []
    assert app.state.dishka_container is container

    async def scenario() -> None:
        try:
            async with (
                asyncio.timeout(5),
                app.router.lifespan_context(app),
                AsyncClient(
                    transport=ASGITransport(app), base_url="http://test", timeout=5
                ) as client,
            ):
                assert (await client.get("/read")).json() == 1
        finally:
            async with asyncio.timeout(5):
                await container.close()

    asyncio.run(scenario())
    assert lifecycle.closed == [1]
