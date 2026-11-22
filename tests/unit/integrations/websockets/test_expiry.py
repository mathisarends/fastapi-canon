import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import time

import fastapi_canon.integrations.websockets.connection as module
import pytest
from fastapi_canon.integrations.websockets import (
    Inject,
    RpcChannel,
    RpcConnectionClose,
    RpcConnectionExpiry,
    RpcMiddleware,
    RpcSocket,
)
from fastapi_canon.integrations.websockets.definition import RpcDefinition
from fastapi_canon.integrations.websockets.testing import (
    InMemorySocket,
)


class Timer:
    def __init__(self, timestamp, callback):
        self.timestamp = timestamp
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def expiry_clock(monkeypatch):
    timers = asyncio.Queue()

    def schedule(timestamp, callback):
        timer = Timer(timestamp, callback)
        timers.put_nowait(timer)
        return timer

    monkeypatch.setattr(module, "_schedule_expiry", schedule)
    return timers


def expiring_service(timestamp, *, middleware=()):
    channel = RpcChannel("expiry")

    @channel.method()
    async def ping() -> str:
        return "pong"

    @channel.stream()
    async def frames() -> AsyncIterator[bytes]:
        await asyncio.Future()
        yield b"frame"

    @channel.socket()
    async def raw(socket: Inject[RpcSocket]) -> None:
        await asyncio.Future()

    async def authenticate(handshake):
        return {RpcConnectionExpiry: RpcConnectionExpiry(timestamp)}

    service = RpcDefinition(middleware=middleware)
    service.socket("/rpc", channels=[channel], before_accept=authenticate)
    service.stream("/binary", frames, before_accept=authenticate)
    service.socket("/raw", raw, before_accept=authenticate)
    return service


@asynccontextmanager
async def served(service, path):
    socket = InMemorySocket(path)
    serving = asyncio.create_task(service.serve(socket))
    try:
        await asyncio.wait_for(socket.wait_handshake(), 1)
        yield socket.client, serving
    finally:
        await socket.client_disconnect()
        await asyncio.wait_for(asyncio.gather(serving, return_exceptions=True), 1)


@pytest.mark.parametrize("path", ["/rpc", "/binary", "/raw"])
async def test_expiry_closes_every_endpoint_with_exact_reason(path, expiry_clock):
    before = asyncio.all_tasks()
    opened, closed = asyncio.Queue(), []

    class Instrumentation(RpcMiddleware):
        async def connection_opened(self, connection):
            opened.put_nowait(connection)

        @asynccontextmanager
        async def connection(self, scope):
            try:
                yield
            finally:
                closed.append(scope.connection)

    service = expiring_service(1000, middleware=[Instrumentation()])
    async with served(service, path) as (client, serving):
        connection = await opened.get()
        timer = await expiry_clock.get()
        assert connection.expires_at == timer.timestamp == 1000
        timer.callback()
        await asyncio.wait_for(serving, 1)
        assert client.close_code == 1008
        assert client.close_reason == "credentials_expired"
        assert closed[0].close_code == RpcConnectionClose.POLICY_VIOLATION
        assert closed[0].close_reason == "credentials_expired"
        assert connection._expiry_timer is None
    assert not asyncio.all_tasks() - before


async def test_past_expiry_is_immediate_and_reported():
    service = expiring_service(0)
    socket = InMemorySocket("/rpc")
    serving = asyncio.create_task(service.serve(socket))
    await asyncio.wait_for(serving, 1)
    assert socket.accepted
    assert socket.client.close_code == 1008
    assert socket.client.close_reason == "credentials_expired"


async def test_expiry_can_be_renewed_or_removed_and_disconnect_cancels_timer(
    expiry_clock,
):
    opened = asyncio.Queue()

    class Instrumentation(RpcMiddleware):
        async def connection_opened(self, connection):
            opened.put_nowait(connection)

    service = expiring_service(1000, middleware=[Instrumentation()])
    async with served(service, "/rpc") as (client, serving):
        connection = await opened.get()
        first = await expiry_clock.get()
        connection.expire_at(2000)
        second = await expiry_clock.get()
        assert first.cancelled and second.timestamp == 2000
        connection.expires_at = None
        assert second.cancelled and connection._expiry_timer is None
        connection.expire_at(3000)
        third = await expiry_clock.get()
        await client.close()
        await asyncio.wait_for(serving, 1)
        assert third.cancelled and connection._expiry_timer is None
        third.callback()  # A stale callback cannot reopen or change the close.
        assert client.close_code == 1000


async def test_expiry_waits_for_the_wall_clock_deadline():
    loop = asyncio.get_running_loop()
    started = loop.time()
    service = expiring_service(time() + 0.2)
    async with served(service, "/rpc") as (client, serving):
        await asyncio.wait_for(serving, 5)
        assert loop.time() - started >= 0.15
        assert client.close_code == 1008


@pytest.mark.parametrize("timestamp", [True, float("nan"), float("inf"), "token"])
def test_invalid_expiry_rejected(timestamp):
    with pytest.raises(ValueError, match="Unix timestamp"):
        RpcConnectionExpiry(timestamp)


@pytest.mark.parametrize(
    ("path", "phase"),
    [
        (path, phase)
        for path in ("/rpc", "/binary", "/raw")
        for phase in ("activity", "connection")
    ]
    + [("/binary", "dependency"), ("/raw", "dependency")],
)
async def test_expiry_interrupts_accepted_connection_initialization(
    path, phase, expiry_clock
):
    entered, cleaned = asyncio.Event(), asyncio.Event()
    before = asyncio.all_tasks()

    async def block():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    class Dependency:
        pass

    class Resolver:
        @asynccontextmanager
        async def enter_connection(self, values):
            if phase == "connection":
                await block()
            yield self

        async def resolve(self, dependency):
            assert dependency is Dependency
            await block()

    class Instrumentation(RpcMiddleware):
        async def connection_opened(self, connection):
            if phase == "activity":
                await block()

    channel = RpcChannel("expiry")

    @channel.stream()
    async def frames(dependency: Inject[Dependency]) -> AsyncIterator[bytes]:
        raise AssertionError("Expired connection must not start a stream")
        yield b""

    @channel.socket()
    async def raw(socket: Inject[RpcSocket], dependency: Inject[Dependency]) -> None:
        raise AssertionError("Expired connection must not start a handler")

    async def authenticate(handshake):
        return {RpcConnectionExpiry: RpcConnectionExpiry(1000)}

    service = RpcDefinition(middleware=[Instrumentation()])
    service.socket("/rpc", channels=[channel], before_accept=authenticate)
    service.stream("/binary", frames, before_accept=authenticate)
    service.socket("/raw", raw, before_accept=authenticate)
    socket = InMemorySocket(path)
    serving = asyncio.create_task(service.serve(socket, resolver=Resolver()))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        (await expiry_clock.get()).callback()
        await asyncio.wait_for(serving, 1)
        assert cleaned.is_set()
        assert socket.client.close_code == 1008
        assert socket.client.close_reason == "credentials_expired"
        assert not serving.cancelled()
        assert not asyncio.all_tasks() - before
    finally:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
