import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from dishka import Provider, Scope, provide
from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from starlette.types import Message

from fastapi_canon import (
    APIRouter,
    Composition,
    Error,
    Feature,
    export_contracts,
)
from fastapi_canon.exceptions import ConfigurationError
from fastapi_canon.integrations.websockets import (
    Inject,
    ProtocolDefinitionError,
    RpcChannel,
    RpcConnection,
)


class Rename(BaseModel):
    title: str


class MissingProject(Exception):
    pass


class ProjectPath(BaseModel):
    project_id: UUID


class Store:
    def __init__(self, identifier: int) -> None:
        self.identifier = identifier


@pytest.mark.parametrize("feature_first", [False, True])
@pytest.mark.parametrize("transport", ["http", "sse", "rpc"])
def test_feature_collects_errors_from_routes_declared_after_it(
    feature_first: bool, transport: str
) -> None:
    missing = Error(MissingProject, status=404, code="project_missing", title="Missing")
    router = APIRouter()
    feature = Feature(name="projects", routers=[router]) if feature_first else None

    if transport == "http":

        @router.get("/project", raises=[missing])
        async def project() -> None:
            raise MissingProject("Private storage details")

    elif transport == "sse":

        @router.sse("/project", raises=[missing])
        async def events() -> AsyncIterator[int]:
            raise MissingProject("Private stream details")
            yield 1  # pragma: no cover

    else:
        channel = RpcChannel()

        @channel.method(raises=[missing])
        async def project() -> None:
            raise MissingProject("Private RPC details")

        router.include_channel(channel, path="/project")

    if feature is None:
        feature = Feature(name="projects", routers=[router])
    assert feature.errors == (missing,)
    composition = Composition(features=[feature])
    app = composition.build(FastAPI())
    with TestClient(app, raise_server_exceptions=False) as client:
        if transport == "rpc":
            with client.websocket_connect("/project") as socket:
                socket.send_json({"jsonrpc": "2.0", "id": 1, "method": "project"})
                payload = socket.receive_json()["error"]["data"]["problem"]
        else:
            response = client.get("/project")
            if transport == "http":
                assert response.status_code == 404
                payload = response.json()
            else:
                data = response.text.split("data: ", 1)[1].splitlines()[0]
                payload = json.loads(data)
    assert payload["code"] == "project_missing"
    assert "Private" not in json.dumps(payload)


@pytest.mark.parametrize("scope", [Scope.APP, Scope.SESSION, Scope.REQUEST])
def test_composed_channels_resolve_services_and_close_their_scopes(
    scope: Scope,
) -> None:
    opened: list[int] = []
    closed: list[int] = []

    class Stores(Provider):
        @provide(scope=scope)
        async def store(self) -> AsyncIterator[Store]:
            identifier = len(opened) + 1
            opened.append(identifier)
            try:
                yield Store(identifier)
            finally:
                closed.append(identifier)

    channel = RpcChannel()

    @channel.method("read")
    async def read(store: Inject[Store]) -> int:
        return store.identifier

    router = APIRouter()
    router.include_channel(channel, path="/session")
    composition = Composition(
        features=[Feature(name="stores", routers=[router], providers=[Stores()])]
    )
    app = FastAPI()
    assert not composition.check(app).has_errors
    assert opened == closed == []
    composition.build(app)

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            async with _connection(app, "/session") as (incoming, outgoing):
                await _send(incoming, 1, "read")
                assert (await _receive(outgoing))["result"] == 1
                assert closed == ([1] if scope is Scope.REQUEST else [])
                await _send(incoming, 2, "read")
                assert (await _receive(outgoing))["result"] == (
                    2 if scope is Scope.REQUEST else 1
                )
            assert closed == ([] if scope is Scope.APP else opened)

    asyncio.run(scenario())
    assert opened == closed == ([1, 2] if scope is Scope.REQUEST else [1])


@pytest.mark.parametrize("include_provider", [False, True])
def test_composed_channels_preserve_an_explicit_resolver(
    include_provider: bool,
) -> None:
    class Stores(Provider):
        @provide(scope=Scope.APP)
        def store(self) -> Store:
            return Store(1)

    async def resolve(dependency: type[Any]) -> Store:
        assert dependency is Store
        return Store(42)

    channel = RpcChannel()

    @channel.method("read")
    async def read(store: Inject[Store]) -> int:
        return store.identifier

    router = APIRouter()
    router.include_channel(channel, path="/session", resolver=resolve)
    composition = Composition(
        features=[
            Feature(
                name="stores",
                routers=[router],
                providers=[Stores()] if include_provider else [],
            )
        ]
    )
    app = FastAPI()
    report = composition.check(app)
    assert not report.has_errors
    assert any(
        check.name.startswith("rpc.method_dependencies:") and check.status == "skipped"
        for check in report.checks
    )
    composition.build(app)

    async def scenario() -> int:
        async with (
            app.router.lifespan_context(app),
            _connection(app, "/session") as (incoming, outgoing),
        ):
            await _send(incoming, 1, "read")
            return (await _receive(outgoing))["result"]

    assert asyncio.run(scenario()) == 42


def test_rpc_preflight_accepts_context_and_hook_values_without_providers() -> None:
    events: list[str] = []

    async def store_hook() -> Store:
        events.append("hook")
        return Store(42)

    async def context(store: Inject[Store]) -> Rename:
        events.append("context")
        return Rename(title=str(store.identifier))

    channel = RpcChannel()

    @channel.method("read")
    async def read(
        store: Inject[Store], data: Inject[Rename], connection: Inject[RpcConnection]
    ) -> str:
        return f"{store.identifier}:{data.title}:{connection.path}"

    router = APIRouter()
    router.include_channel(
        channel, path="/session", context=context, hooks=(store_hook,)
    )
    composition = Composition(features=[Feature(name="stores", routers=[router])])
    app = FastAPI()
    report = composition.check(app)
    assert report.complete and not report.has_errors
    composition.build(app)
    assert events == []

    async def scenario() -> str:
        async with (
            app.router.lifespan_context(app),
            _connection(app, "/session") as (incoming, outgoing),
        ):
            await _send(incoming, 1, "read")
            return str((await _receive(outgoing))["result"])

    assert asyncio.run(scenario()) == "42:42:/session"
    assert events == ["hook", "context"]


def test_composed_channels_share_one_connection_and_keep_path_context() -> None:
    projects = RpcChannel()
    tasks = RpcChannel()

    @projects.method("rename")
    async def rename(data: Rename, connection: Inject[RpcConnection]) -> str:
        return f"{connection.path_params['project_id']}: {data.title}"

    @tasks.method("create")
    async def create() -> str:
        return "created"

    session = RpcChannel()
    session.include_channel(projects, namespace="projects")
    session.include_channel(tasks, namespace="tasks")
    router = APIRouter(prefix="/projects")
    router.include_channel(session, path="/{project_id}/session")
    app = Composition(features=[Feature(name="projects", routers=[router])]).build(
        FastAPI()
    )

    async def scenario() -> tuple[str, str]:
        async with _connection(app, "/projects/42/session") as (incoming, outgoing):
            await _send(incoming, 1, "projects.rename", {"title": "Updated"})
            renamed = await _receive(outgoing)
            assert renamed == {
                "jsonrpc": "2.0",
                "id": 1,
                "result": "42: Updated",
            }
            await _send(incoming, 2, "tasks.create")
            created = await _receive(outgoing)
            assert created == {
                "jsonrpc": "2.0",
                "id": 2,
                "result": "created",
            }
            return renamed["result"], created["result"]

    assert asyncio.run(scenario()) == ("42: Updated", "created")


def test_mounting_snapshots_and_reuses_a_channel_at_different_paths() -> None:
    source = RpcChannel()

    @source.method("first")
    async def first() -> str:
        return "first"

    router = APIRouter()
    router.include_channel(source, path="/first", namespace="first")

    @source.method("later")
    async def later() -> str:
        return "later"

    router.include_channel(source, path="/second", namespace="second")
    app = FastAPI()
    app.include_router(router)

    async def scenario() -> None:
        async with _connection(app, "/first") as (incoming, outgoing):
            await _send(incoming, 1, "first.later")
            assert (await _receive(outgoing))["error"]["code"] == -32601
        async with _connection(app, "/second") as (incoming, outgoing):
            await _send(incoming, 2, "second.later")
            assert (await _receive(outgoing))["result"] == "later"

    asyncio.run(scenario())
    document = export_contracts(app, kinds=["openrpc"]).openrpc
    assert document is not None
    assert {method["name"] for method in document["methods"]} == {
        "first.first",
        "second.first",
        "second.later",
    }


def test_typed_channel_paths_include_the_router_prefix() -> None:
    channel = RpcChannel()

    @channel.method("read")
    async def read(path: Inject[ProjectPath]) -> UUID:
        return path.project_id

    router = APIRouter(prefix="/projects/{project_id}")
    router.include_channel(channel, path="/session", path_model=ProjectPath)
    app = FastAPI()
    app.include_router(router)
    project_id = "43fcb13b-d963-4a25-9bd1-e14114124378"

    async def scenario() -> str:
        async with _connection(app, f"/projects/{project_id}/session") as (
            incoming,
            outgoing,
        ):
            await _send(incoming, 1, "read")
            return (await _receive(outgoing))["result"]

    assert asyncio.run(scenario()) == project_id


def test_nested_channel_errors_are_collected_and_do_not_close_the_connection() -> None:
    missing = Error(
        MissingProject, status=404, code="project_missing", title="Project missing"
    )
    projects = RpcChannel()

    @projects.method("read", raises=[missing])
    async def read() -> None:
        raise MissingProject("private database context")

    @projects.method("ping")
    async def ping() -> str:
        return "pong"

    session = RpcChannel()
    session.include_channel(projects, namespace="projects")
    router = APIRouter()
    router.include_channel(session, path="/session")
    parent = APIRouter(prefix="/api")
    parent.include_router(router)
    app = Composition(features=[Feature(name="projects", routers=[parent])]).build(
        FastAPI()
    )

    async def scenario() -> str:
        async with _connection(app, "/api/session") as (incoming, outgoing):
            await _send(incoming, 1, "projects.read")
            error = (await _receive(outgoing))["error"]
            assert error["data"]["problem"] == {
                "type": "urn:fastapi-canon:problem/project_missing",
                "title": "Project missing",
                "code": "project_missing",
            }
            assert "private" not in json.dumps(error)
            await _send(incoming, 2, "projects.ping")
            return (await _receive(outgoing))["result"]

    assert asyncio.run(scenario()) == "pong"


def test_message_stream_cancel_keeps_methods_available_and_cleans_up() -> None:
    cleaned = asyncio.Event()
    projects = RpcChannel()

    @projects.stream("events")
    async def events() -> AsyncIterator[str]:
        try:
            yield "updated"
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    @projects.method("ping")
    async def ping() -> str:
        return "pong"

    session = RpcChannel()
    session.include_channel(projects, namespace="projects")
    router = APIRouter()
    router.include_channel(session, path="/session")
    app = FastAPI()
    app.include_router(router)

    async def scenario() -> str:
        async with _connection(app, "/session") as (incoming, outgoing):
            await _send(incoming, 1, "projects.events.open")
            stream_id = (await _receive(outgoing))["result"]["streamId"]
            assert (await _receive(outgoing))["params"] == {
                "streamId": stream_id,
                "payload": "updated",
            }
            await _send(incoming, 2, "projects.events.cancel", {"streamId": stream_id})
            assert await _receive(outgoing) == {
                "jsonrpc": "2.0",
                "id": 2,
                "result": None,
            }
            assert cleaned.is_set()
            await _send(incoming, 3, "projects.ping")
            return (await _receive(outgoing))["result"]

    assert asyncio.run(scenario()) == "pong"
    assert cleaned.is_set()


def test_disconnect_closes_an_active_message_stream() -> None:
    cleaned = asyncio.Event()
    channel = RpcChannel()

    @channel.stream("events")
    async def events() -> AsyncIterator[str]:
        try:
            yield "ready"
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    router = APIRouter()
    router.include_channel(channel, path="/session")
    app = FastAPI()
    app.include_router(router)

    async def scenario() -> None:
        async with _connection(app, "/session") as (incoming, outgoing):
            await _send(incoming, 1, "events.open")
            assert "streamId" in (await _receive(outgoing))["result"]
            assert (await _receive(outgoing))["params"]["payload"] == "ready"
        assert cleaned.is_set()

    asyncio.run(scenario())
    assert cleaned.is_set()


def test_router_and_mount_dependencies_run_once_before_accept() -> None:
    calls: list[str] = []

    async def authorize(project_id: str) -> None:
        calls.append(project_id)

    router = APIRouter(prefix="/projects", dependencies=[Depends(authorize)])
    router.include_channel(
        RpcChannel(), path="/{project_id}/session", dependencies=[Depends(authorize)]
    )
    app = FastAPI()
    app.include_router(router)

    async def scenario() -> tuple[str, ...]:
        async with _connection(app, "/projects/42/session"):
            return tuple(calls)

    assert asyncio.run(scenario()) == ("42",)


def test_methods_execute_concurrently_on_the_same_connection() -> None:
    channel = RpcChannel()
    entered = asyncio.Event()
    release = asyncio.Event()

    @channel.method("slow")
    async def slow() -> str:
        entered.set()
        await release.wait()
        return "slow finished"

    @channel.method("fast")
    async def fast() -> str:
        release.set()
        return "fast finished"

    router = APIRouter()
    router.include_channel(channel, path="/session")
    app = FastAPI()
    app.include_router(router)

    async def scenario() -> dict[int, str]:
        async with _connection(app, "/session") as (incoming, outgoing):
            await _send(incoming, 1, "slow")
            await asyncio.wait_for(entered.wait(), timeout=2)
            await _send(incoming, 2, "fast")
            first = await _receive(outgoing)
            second = await _receive(outgoing)
            return {first["id"]: first["result"], second["id"]: second["result"]}

    assert asyncio.run(scenario()) == {1: "slow finished", 2: "fast finished"}


def test_sse_serializes_events_and_sends_terminal_completion() -> None:
    router = APIRouter(prefix="/projects")

    @router.get("/health")
    async def health() -> str:
        return "ok"

    @router.sse("/{project_id}/events", operation_id="project_events")
    async def events(project_id: str) -> AsyncIterator[Rename]:
        yield Rename(title=project_id)

    app = Composition(features=[Feature(name="projects", routers=[router])]).build(
        FastAPI()
    )
    with TestClient(app) as client:
        assert client.get("/projects/health").json() == "ok"
        response = client.get("/projects/42/events")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'data: {"title":"42"}' in response.text
    assert "event: canon.complete" in response.text
    assert (
        app.openapi()["paths"]["/projects/{project_id}/events"]["get"][
            "x-canon-stream"
        ]["completeEvent"]
        == "canon.complete"
    )


def test_sse_raises_registers_terminal_errors() -> None:
    missing = Error(
        MissingProject, status=404, code="project_missing", title="Project missing"
    )
    router = APIRouter()

    @router.sse("/events", raises=[missing])
    async def events() -> AsyncIterator[str]:
        yield "first"
        raise MissingProject("private failure")

    app = Composition(features=[Feature(name="projects", routers=[router])]).build(
        FastAPI()
    )
    with TestClient(app) as client:
        response = client.get("/events")
    assert "event: canon.problem" in response.text
    problem_frame = response.text.split("event: canon.problem\n", 1)[1]
    problem = json.loads(problem_frame.split("data: ", 1)[1].split("\n", 1)[0])
    assert problem["code"] == "project_missing"
    assert "canon.complete" not in response.text
    assert "private failure" not in response.text


def test_binary_streams_and_typed_sockets_are_declared_on_the_router() -> None:
    class Stores(Provider):
        @provide(scope=Scope.REQUEST)
        def store(self) -> Store:
            return Store(3)

    router = APIRouter(prefix="/projects", namespace="projects")

    @router.binary_stream("/{project_id}/preview", content_type="image/jpeg")
    async def preview(project_id: int, store: Inject[Store]) -> AsyncIterator[bytes]:
        yield f"{project_id}:{store.identifier}".encode()

    @router.socket("/{project_id}/rename")
    async def rename(message: Rename, store: Inject[Store]) -> Rename:
        return Rename(title=f"{message.title} ({store.identifier})")

    app = Composition(
        features=[Feature(name="projects", routers=[router], providers=[Stores()])]
    ).build(FastAPI())

    with TestClient(app) as client:
        with client.websocket_connect("/projects/7/preview") as socket:
            assert socket.receive_bytes() == b"7:3"
        with client.websocket_connect("/projects/7/rename") as socket:
            socket.send_json({"title": "Draft"})
            assert socket.receive_json() == {"title": "Draft (3)"}
    document = export_contracts(app, kinds=("openrpc",)).documents["openrpc"]
    assert [
        (stream["name"], stream["url"], stream["contentType"])
        for stream in document["x-rpckit-binary-streams"]
    ] == [("projects.preview", "{baseUrl}/projects/{project_id}/preview", "image/jpeg")]
    assert [
        (socket["name"], socket["url"]) for socket in document["x-rpckit-sockets"]
    ] == [("projects.rename", "{baseUrl}/projects/{project_id}/rename")]


def test_router_channel_mounts_methods_declared_after_it() -> None:
    router = APIRouter(prefix="/projects", namespace="projects")
    session = router.channel("/{project_id}/session")

    @session.method()
    async def rename(data: Rename, connection: Inject[RpcConnection]) -> str:
        return f"{connection.path_params['project_id']}: {data.title}"

    app = Composition(features=[Feature(name="projects", routers=[router])]).build(
        FastAPI()
    )

    async def scenario() -> Any:
        async with _connection(app, "/projects/42/session") as (incoming, outgoing):
            await _send(incoming, 1, "projects.rename", {"title": "Updated"})
            return (await _receive(outgoing))["result"]

    assert asyncio.run(scenario()) == "42: Updated"
    with pytest.raises(ProtocolDefinitionError, match="frozen"):

        @session.method()
        async def late() -> str:
            return "never mounted"


def test_channels_with_streams_or_sockets_point_to_router_declarations() -> None:
    channel = RpcChannel("media")

    @channel.stream(content_type="application/octet-stream")
    async def preview() -> AsyncIterator[bytes]:
        yield b""

    router = APIRouter()
    with pytest.raises(ConfigurationError, match=r"@router.binary_stream"):
        router.include_channel(channel, path="/media")
    assert router.routes == []


def test_duplicate_websocket_paths_fail_before_mutating_routes() -> None:
    router = APIRouter(prefix="/projects")

    @router.websocket("/{project_id}/session")
    async def raw(websocket: WebSocket) -> None:
        await websocket.accept()

    routes = tuple(router.routes)
    with pytest.raises(ConfigurationError, match="Duplicate WebSocket path"):
        router.include_channel(RpcChannel(), path="/{other_id}/session")
    assert tuple(router.routes) == routes


def test_separate_websocket_urls_and_shared_http_urls_remain_reachable() -> None:
    first = APIRouter()
    second = APIRouter()
    first.get("/one", operation_id="http.one")(lambda: "http")
    channel = RpcChannel()

    @channel.method("read")
    async def read() -> str:
        return "rpc"

    first.include_channel(channel, path="/one")

    @second.websocket("/two")
    async def native(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("native")
        await websocket.receive()

    composition = Composition(
        features=[
            Feature(name="one", routers=[first]),
            Feature(name="two", routers=[second]),
        ]
    )
    app = composition.build(FastAPI())

    async def scenario() -> None:
        async with asyncio.timeout(5), app.router.lifespan_context(app):
            async with _connection(app, "/one") as (incoming, outgoing):
                await _send(incoming, 1, "read")
                assert (await _receive(outgoing))["result"] == "rpc"
            async with _connection(app, "/two") as (_, outgoing):
                assert (await outgoing.get())["text"] == "native"
            async with AsyncClient(
                transport=ASGITransport(app), base_url="http://test", timeout=5
            ) as client:
                assert (await client.get("/one")).json() == "http"

    asyncio.run(scenario())
    assert not composition.check(app).has_errors


@asynccontextmanager
async def _connection(
    app: FastAPI, path: str, subprotocols: list[str] | None = None
) -> AsyncIterator[tuple[asyncio.Queue[Message], asyncio.Queue[Message]]]:
    incoming: asyncio.Queue[Message] = asyncio.Queue()
    outgoing: asyncio.Queue[Message] = asyncio.Queue()
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "subprotocols": subprotocols or [],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "state": {},
        "extensions": {"websocket.http.response": {}},
    }
    task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
    try:
        async with asyncio.timeout(5):
            await incoming.put({"type": "websocket.connect"})
            assert (await outgoing.get())["type"] == "websocket.accept"
            yield incoming, outgoing
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2)


async def _send(
    incoming: asyncio.Queue[Message],
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> None:
    await incoming.put(
        {
            "type": "websocket.receive",
            "text": json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            ),
        }
    )


async def _receive(outgoing: asyncio.Queue[Message]) -> Any:
    message = await outgoing.get()
    assert message["type"] == "websocket.send", message
    return json.loads(message["text"])
