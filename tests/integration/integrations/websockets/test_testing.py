import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from dishka import Provider, Scope, provide
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketException
from pydantic import BaseModel

from fastapi_canon import APIRouter, Composition, Error, Feature
from fastapi_canon.integrations.websockets import Inject, RpcChannel, RpcRejection
from fastapi_canon.integrations.websockets.adapters.dishka import Dishka
from fastapi_canon.integrations.websockets.testing import (
    RpcTestClient,
    RpcTestConnectionClosed,
    RpcTestError,
    RpcTestStream,
)


class Missing(Exception):
    pass


class ReadParams(BaseModel):
    fail: bool = False


class Echo(BaseModel):
    value: str


@dataclass(frozen=True)
class Resource:
    value: str


class Resources(Provider):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    @provide(scope=Scope.SESSION)
    async def resource(self) -> AsyncIterator[Resource]:
        self.events.append("opened")
        try:
            yield Resource("resolved")
        finally:
            self.events.append("closed")


@pytest.mark.parametrize("mounted", [False, True])
async def test_composed_asgi_client_runs_auth_di_errors_and_cleanup(
    mounted: bool,
) -> None:
    events: list[str] = []
    channel = RpcChannel("items")
    missing = Error(Missing, status=404, code="missing", title="Missing")

    @channel.method(raises=[missing])
    async def read(params: ReadParams, resource: Inject[Resource]) -> str:
        if params.fail:
            raise Missing()
        return resource.value

    async def authenticate(socket: WebSocket, admission: Inject[int]) -> str:
        if socket.headers.get("authorization") != "Bearer test":
            raise WebSocketException(1008, "denied")
        events.append(socket.query_params["client"])
        return f"owner-{admission}"

    async def admitted() -> int:
        return 7

    @channel.method
    async def role(admission: Inject[int]) -> int:
        return admission

    router = APIRouter(prefix="/api")
    router.include_channel(
        channel, path="/rpc", context=authenticate, hooks=[admitted], resolver=Dishka()
    )
    app = Composition(
        features=[
            Feature(name="items", routers=[router], providers=[Resources(events)])
        ]
    ).build(FastAPI())
    target = FastAPI() if mounted else app
    if mounted:
        target.mount("/service", app)
    path = ("/service" if mounted else "") + "/api/rpc?client=integration"
    async with asyncio.timeout(5), app.router.lifespan_context(app):
        async with RpcTestClient(
            target, path, headers={"authorization": "Bearer test"}
        ) as client:
            assert await client.request("items.read") == "resolved"
            assert await client.request("items.role") == 7
            with pytest.raises(RpcTestError) as failure:
                await client.request("items.read", {"fail": True})
            assert failure.value.problem is not None
            assert failure.value.problem.code == "missing"
        assert events == ["integration", "opened", "closed"]
        async with RpcTestClient(target, path) as denied:
            with pytest.raises(RpcTestConnectionClosed):
                await denied.request("items.read")
        assert events == ["integration", "opened", "closed"]


async def test_router_only_binary_stream_and_raw_socket_use_the_registered_routes() -> (
    None
):
    router = APIRouter(prefix="/api")

    @router.binary_stream("/{item_id}/frames")
    async def frames(item_id: int) -> AsyncIterator[bytes]:
        yield str(item_id).encode()

    @router.socket("/echo")
    async def echo(message: Echo) -> Echo:
        return message

    async with asyncio.timeout(5):
        async with RpcTestStream(router, "/api/7/frames") as stream:
            assert await stream.next_frame() == b"7"
        async with RpcTestClient(router, "/api/echo") as socket:
            await socket.send_message('{"value":"echo"}')
            assert await socket.next_message() == '{"value":"echo"}'


def test_asgi_client_rejects_in_memory_dependency_overrides() -> None:
    with pytest.raises(ValueError, match="ASGI application"):
        RpcTestClient(APIRouter(), "/rpc", context="bypassed authentication")


async def test_http_handshake_denial_keeps_the_production_status_and_body() -> None:
    async def deny() -> str:
        raise HTTPException(401, "sign in")

    router = APIRouter()
    channel = router.channel("/rpc", context=deny)

    @channel.method
    async def ping() -> str:
        return "pong"

    async with asyncio.timeout(5), RpcTestClient(router, "/rpc") as client:
        with pytest.raises(RpcTestConnectionClosed):
            await client.request("ping")
        assert client.socket.rejection == (
            RpcRejection.UNAUTHORIZED,
            '{"detail":"sign in"}',
        )


async def test_asgi_application_failure_unblocks_pending_requests() -> None:
    channel = RpcChannel("items")

    @channel.method
    async def ping() -> str:
        return "pong"

    async def broken_context() -> str:
        raise RuntimeError("broken admission")

    router = APIRouter()
    router.include_channel(channel, path="/rpc", context=broken_context)
    async with asyncio.timeout(5):
        with pytest.raises(RuntimeError, match="broken admission"):
            async with RpcTestClient(router, "/rpc") as client:
                with pytest.raises(RpcTestConnectionClosed):
                    await client.request("items.ping")
