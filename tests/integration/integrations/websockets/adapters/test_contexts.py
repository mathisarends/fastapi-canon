import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from dishka import Provider, Scope, provide
from fastapi import FastAPI, Header, WebSocket
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from fastapi_canon import APIRouter, Composition, Feature, Inject
from fastapi_canon.integrations.websockets import (
    CanonWebSocketRouter,
    RpcChannel,
    RpcRejection,
)
from fastapi_canon.integrations.websockets.adapters.dishka import Dishka


@dataclass
class Resolver:
    closed: bool = False


@dataclass(frozen=True)
class Access:
    project_id: int


@dataclass(frozen=True)
class Connection:
    access: Access


class Denied(Exception):
    pass


@pytest.mark.parametrize("transport", ["rpc", "stream", "custom", "shared"])
def test_hooks_share_values_and_close_resources_before_serving(transport: str) -> None:
    events: list[str] = []
    resources: list[Resolver] = []

    class Resources(Provider):
        @provide(scope=Scope.REQUEST)
        async def resolver(self) -> AsyncIterator[Resolver]:
            resource = Resolver()
            resources.append(resource)
            events.append("open")
            try:
                yield resource
            finally:
                resource.closed = True
                events.append("close")

    async def authorize(
        project_id: int,
        resolver: Inject[Resolver],
        token: Annotated[str, Header(alias="X-Token")],
    ) -> Access:
        assert not resolver.closed
        assert token == "allowed"
        events.append("authorize")
        return Access(project_id)

    async def context(first: Inject[Access], second: Inject[Access]) -> Connection:
        assert first is second
        events.append("context")
        return Connection(first)

    async def before_accept(handshake: Any) -> None:
        assert resources[-1].closed
        assert events[-1] == "close"
        events.append("accept")

    channel = RpcChannel("projects")

    @channel.method()
    async def identifier(connection: Inject[Connection], access: Inject[Access]) -> int:
        assert connection.access is access
        return access.project_id

    streams = RpcChannel("frames")

    @streams.stream(content_type="application/octet-stream")
    async def frames(connection: Inject[Connection]) -> AsyncIterator[bytes]:
        yield str(connection.access.project_id).encode()

    async def serve(websocket: WebSocket, connection: Inject[Connection]) -> None:
        assert resources[-1].closed
        await websocket.accept()
        await websocket.send_json(connection.access.project_id)
        await websocket.close()

    router: APIRouter | CanonWebSocketRouter
    if transport == "shared":
        router = APIRouter(prefix="/projects")
        router.include_channel(
            channel,
            path="/{project_id}",
            context=context,
            hooks=(authorize,),
            resolver=Dishka(),
            before_accept=before_accept,
        )
    else:
        router = CanonWebSocketRouter(
            context=context,
            hooks=(authorize,),
            resolver=Dishka(),
            before_accept=before_accept,
        )
        if transport == "stream":
            router.stream("/projects/{project_id}", frames)
        else:
            router.socket(
                "/projects/{project_id}",
                channels=(channel,),
                serve=serve if transport == "custom" else None,
            )
    app = Composition(
        features=(
            Feature(name="projects", routers=(router,), providers=(Resources(),)),
        )
    ).apply(FastAPI())

    with TestClient(app) as client:
        with client.websocket_connect(
            "/projects/7", headers={"X-Token": "allowed"}
        ) as socket:
            if transport in ("rpc", "shared"):
                socket.send_json(
                    {"jsonrpc": "2.0", "id": 1, "method": "projects.identifier"}
                )
                assert socket.receive_json()["result"] == 7
            elif transport == "stream":
                assert socket.receive_bytes() == b"7"
            else:
                assert socket.receive_json() == 7
        with client.websocket_connect(
            "/projects/8", headers={"X-Token": "allowed"}
        ) as socket:
            if transport in ("rpc", "shared"):
                socket.send_json(
                    {"jsonrpc": "2.0", "id": 2, "method": "projects.identifier"}
                )
                assert socket.receive_json()["result"] == 8
            elif transport == "stream":
                assert socket.receive_bytes() == b"8"
            else:
                assert socket.receive_json() == 8
    assert len(resources) == 2
    assert resources[0] is not resources[1]
    expected = ["open", "authorize", "context", "close"]
    if transport != "custom":
        expected.append("accept")
    assert events == expected * 2


@pytest.mark.parametrize("failure", ["hook", "context", "cleanup"])
def test_failed_preparation_cleans_up_without_di_fallback(failure: str) -> None:
    events: list[str] = []

    class Resources(Provider):
        @provide(scope=Scope.REQUEST)
        async def resolver(self) -> AsyncIterator[Resolver]:
            events.append("open")
            try:
                yield Resolver()
            finally:
                events.append("close")
                if failure == "cleanup":
                    raise Denied("cleanup failed")

        @provide(scope=Scope.APP)
        def fallback(self) -> Access:
            events.append("fallback")
            return Access(0)

    async def authorize(resolver: Inject[Resolver]) -> Access:
        if failure == "hook":
            raise Denied("access denied")
        return Access(7)

    async def context(access: Inject[Access]) -> Connection:
        if failure == "context":
            raise Denied("context failed")
        return Connection(access)

    router = CanonWebSocketRouter(
        context=context,
        hooks=(authorize,),
        resolver=Dishka(),
        rejections={Denied: RpcRejection.FORBIDDEN},
    )
    router.socket("/rpc", channels=(RpcChannel("projects"),))
    app = Composition(
        features=(
            Feature(name="projects", routers=(router,), providers=(Resources(),)),
        )
    ).apply(FastAPI())
    if failure == "cleanup":
        with (
            TestClient(app) as client,
            pytest.raises(ExceptionGroup, match="Cleanup context errors"),
            client.websocket_connect("/rpc"),
        ):
            pytest.fail("Failed cleanup must not accept a connection")
    else:
        with (
            TestClient(app) as client,
            pytest.raises(WebSocketDenialResponse) as denied,
            client.websocket_connect("/rpc"),
        ):
            pytest.fail("Failed preparation must not accept a connection")
        assert denied.value.status_code == 403
    assert events == ["open", "close"]


async def test_cancelled_preparation_releases_request_resources() -> None:
    entered = asyncio.Event()
    released: list[bool] = []

    class Resources(Provider):
        @provide(scope=Scope.REQUEST)
        async def resolver(self) -> AsyncIterator[Resolver]:
            try:
                yield Resolver()
            finally:
                released.append(True)

    async def context(resolver: Inject[Resolver]) -> Access:
        entered.set()
        await asyncio.Future[None]()
        return Access(7)

    router = CanonWebSocketRouter(context=context, resolver=Dishka())
    router.socket("/rpc", channels=(RpcChannel("projects"),))
    app = Composition(
        features=(
            Feature(name="projects", routers=(router,), providers=(Resources(),)),
        )
    ).apply(FastAPI())
    received: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        received.append(message)

    async with asyncio.timeout(5), app.router.lifespan_context(app):
        task = asyncio.create_task(
            app(
                {
                    "type": "websocket",
                    "path": "/rpc",
                    "headers": [],
                    "query_string": b"",
                    "scheme": "ws",
                },
                receive,
                send,
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert released == [True]
    assert received == []
