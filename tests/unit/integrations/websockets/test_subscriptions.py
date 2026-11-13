import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi_canon.integrations.websockets import (
    RpcChannel,
    RpcErrorBinding,
    RpcLimits,
    RpcMiddleware,
    RpcModel,
)
from fastapi_canon.integrations.websockets.definition import RpcDefinition
from fastapi_canon.integrations.websockets.testing import RpcTestClient

from tests.unit.integrations.websockets.deadlines import ControlledDeadlines
from tests.unit.integrations.websockets.testing import InMemorySocket


@pytest.mark.parametrize("interrupt", ["timeout", "cancel"])
async def test_subscription_ack_is_not_followed_by_a_second_response(
    monkeypatch, interrupt
):
    from fastapi_canon.integrations.websockets.dependencies import as_resolver
    from fastapi_canon.integrations.websockets.envelopes import RpcRequestEnvelope
    from fastapi_canon.integrations.websockets.server import (
        # Verify the internal client-cancellation sentinel.
        CLIENT_CANCEL,
    )
    from fastapi_canon.integrations.websockets.subscriptions import SubscriptionSession

    clock = ControlledDeadlines(monkeypatch)
    exiting, started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    responses = []
    channel = RpcChannel("session")

    class Middleware(RpcMiddleware):
        @asynccontextmanager
        async def request(self, scope):
            yield
            exiting.set()
            await asyncio.Future()

    @channel.stream()
    async def events() -> AsyncIterator[int]:
        started.set()
        await release.wait()
        yield 42

    async def send(message):
        responses.append(json.loads(message))

    session = SubscriptionSession(
        channel.protocol.subscriptions,
        as_resolver(None),
        send,
        limit=1,
        server=channel.create_server(
            limits=RpcLimits(request_timeout=5), middleware=[Middleware()]
        ),
    )
    task = asyncio.create_task(
        session.handle(
            RpcRequestEnvelope(jsonrpc="2.0", id=1, method="session.events.open")
        )
    )
    try:
        async with asyncio.timeout(2):
            _, deadline = await clock.next()
            await exiting.wait()
            await started.wait()
            assert responses == [
                {"jsonrpc": "2.0", "id": 1, "result": {"streamId": "1"}}
            ]
            if interrupt == "timeout":
                clock.expire(deadline)
            else:
                task.cancel(CLIENT_CANCEL)
            assert await task is None
            release.set()
            await session.wait_finished()
            assert responses[1]["params"]["payload"] == 42
            assert responses[2]["params"]["complete"] is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.close()


async def test_deadline_bounds_subscription_handshake_but_does_not_limit_stream(
    monkeypatch,
):
    clock = ControlledDeadlines(monkeypatch)
    entered = asyncio.Event()
    stream_started = asyncio.Event()
    release_stream = asyncio.Event()
    stream_stopped = asyncio.Event()
    channel = RpcChannel("session")

    @channel.method(timeout=None)
    async def occupy() -> None:
        entered.set()
        await asyncio.Future()

    @channel.stream()
    async def events() -> AsyncIterator[str]:
        stream_started.set()
        try:
            await release_stream.wait()
            yield "after handshake"
        finally:
            stream_stopped.set()

    service = RpcDefinition(limits=RpcLimits(max_concurrency=1, request_timeout=5))
    service.socket("/rpc", channels=[channel])
    socket = InMemorySocket("/rpc")
    serving = asyncio.create_task(service.serve(socket))
    try:
        async with asyncio.timeout(2):
            await socket.client_send(
                json.dumps({"jsonrpc": "2.0", "method": "session.occupy", "id": 1})
            )
            await entered.wait()
            await socket.client_send(
                json.dumps({"jsonrpc": "2.0", "method": "session.events.open", "id": 2})
            )
            _, handshake_timeout = await clock.next()
            clock.expire(handshake_timeout)
            response = json.loads(await socket.client_receive())
            assert response["id"] == 2
            assert response["error"]["data"]["code"] == "request_timeout"
            assert not stream_started.is_set()
            await socket.client_send(
                json.dumps(
                    {"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 1}}
                )
            )
            assert (
                json.loads(await socket.client_receive())["error"]["data"]["code"]
                == "request_cancelled"
            )
            await socket.client_send(
                json.dumps({"jsonrpc": "2.0", "method": "session.events.open", "id": 3})
            )
            _, successful_handshake = await clock.next()
            subscription = json.loads(await socket.client_receive())["result"][
                "streamId"
            ]
            await stream_started.wait()
            # A finished timeout has no timer left to affect the subscription task.
            with pytest.raises(RuntimeError, match="finished"):
                successful_handshake.reschedule(asyncio.get_running_loop().time())
            await socket.client_send(
                json.dumps(
                    {"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 3}}
                )
            )
            release_stream.set()
            message = json.loads(await socket.client_receive())
            assert message["params"] == {
                "streamId": subscription,
                "payload": "after handshake",
            }
            terminal = json.loads(await socket.client_receive())
            assert terminal["params"]["complete"] is True
            assert stream_stopped.is_set()
    finally:
        await socket.client_disconnect()
        await asyncio.wait_for(serving, 1)


class SessionParams(RpcModel):
    session_id: str


class SessionEvent(RpcModel):
    value: str


class SessionMissing(Exception):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


@pytest.mark.parametrize("declared", [True, False])
async def test_subscription_failures_use_scoped_bindings(declared, caplog) -> None:
    binding = RpcErrorBinding(SessionMissing, details=SessionParams)
    channel = RpcChannel("session")

    @channel.stream(raises=[binding] if declared else [])
    async def events(params: SessionParams) -> AsyncIterator[SessionEvent]:
        yield SessionEvent(value="first")
        raise SessionMissing(params.session_id)

    @channel.method(raises=[binding])
    async def lookup() -> None: ...

    service = RpcDefinition()
    service.socket("/rpc", channels=[channel])
    document = service.contract(
        title="Sessions", base_url="ws://localhost"
    ).to_openrpc()
    subscription = document["x-rpc-streams"][0]
    assert ("errors" in subscription) is declared
    if declared:
        assert subscription["errors"][0]["x-rpckit-details-schema"] == {
            "$ref": "#/components/schemas/SessionParams"
        }
    async with RpcTestClient(service, "/rpc") as client:
        response = await client.request("session.events.open", {"sessionId": "abc"})
        _, first = await client.next_notification(timeout=1)
        _, terminal = await client.next_notification(timeout=1)
    assert first["payload"] == {"value": "first"}
    assert terminal["streamId"] == response["streamId"]
    assert terminal["complete"] is False
    error = terminal["error"]
    assert error["data"]["code"] == (
        "session_missing" if declared else "internal_error"
    )
    assert error["message"] == ("Session missing" if declared else "Internal error")
    if declared:
        assert error["data"]["details"] == {"sessionId": "abc"}
    else:
        assert "RPC method session.events failed" in caplog.text


async def test_subscription_params_notifications_and_cleanup() -> None:
    async with asyncio.timeout(10):
        channel = RpcChannel("session")
        stopped = asyncio.Event()

        @channel.stream()
        async def events(params: SessionParams) -> AsyncIterator[SessionEvent]:
            try:
                yield SessionEvent(value=params.session_id)
                await asyncio.Future()
            finally:
                stopped.set()

        service = RpcDefinition()
        service.socket("/rpc", channels=(channel,))
        socket = InMemorySocket("/rpc")
        task = asyncio.create_task(service.serve(socket))
        await asyncio.sleep(0)
        await socket.client_send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session.events.open",
                    "params": {"sessionId": "abc"},
                }
            )
        )
        response = json.loads(await asyncio.wait_for(socket.client_receive(), 1))
        subscription_id = response["result"]["streamId"]
        notification = json.loads(await asyncio.wait_for(socket.client_receive(), 1))
        assert notification == {
            "jsonrpc": "2.0",
            "method": "session.events",
            "params": {
                "streamId": subscription_id,
                "payload": {"value": "abc"},
            },
        }
        await socket.client_send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.events.cancel",
                    "params": {"streamId": subscription_id},
                }
            )
        )
        assert (
            json.loads(await asyncio.wait_for(socket.client_receive(), 1))["result"]
            is None
        )
        await asyncio.wait_for(stopped.wait(), 1)
        stopped.clear()
        await socket.client_send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session.events.open",
                    "params": {"sessionId": "second"},
                }
            )
        )
        assert "result" in json.loads(
            await asyncio.wait_for(socket.client_receive(), 1)
        )
        await asyncio.wait_for(socket.client_receive(), 1)
        await socket.client_disconnect()
        await task
        assert stopped.is_set()


async def test_stream_limit_and_contract() -> None:
    async with asyncio.timeout(10):
        channel = RpcChannel("session")

        @channel.stream()
        async def events(params: SessionParams) -> AsyncIterator[SessionEvent]:
            await asyncio.Future()
            yield SessionEvent(value=params.session_id)

        service = RpcDefinition()
        service.socket("/rpc", channels=(channel,))
        document = service.contract(
            title="Session", base_url="ws://localhost"
        ).to_openrpc()
        assert document["x-rpc-streams"][0]["name"] == "session.events"
        socket = InMemorySocket("/rpc")
        task = asyncio.create_task(
            service.serve(socket, limits=RpcLimits(max_streams=1))
        )
        await asyncio.sleep(0)
        for request_id in (1, 2):
            await socket.client_send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "session.events.open",
                        "params": {"sessionId": "abc"},
                    }
                )
            )
            response = json.loads(await asyncio.wait_for(socket.client_receive(), 1))
            assert ("result" in response) is (request_id == 1)
        await socket.client_disconnect()
        await task


@pytest.mark.parametrize("cancel_handshake", [False, True])
async def test_stream_limit_reserves_blocked_handshakes(cancel_handshake):
    async with asyncio.timeout(10):
        from fastapi_canon.integrations.websockets.dependencies import as_resolver
        from fastapi_canon.integrations.websockets.envelopes import RpcRequestEnvelope
        from fastapi_canon.integrations.websockets.subscriptions import (
            SubscriptionSession,
        )

        channel = RpcChannel("session")
        sending, release = asyncio.Event(), asyncio.Event()
        responses = []

        @channel.stream()
        async def events() -> AsyncIterator[int]:
            await asyncio.Future()
            yield 1

        async def send(message):
            if json.loads(message)["id"] == 1:
                sending.set()
                await release.wait()
            responses.append(json.loads(message))

        session = SubscriptionSession(
            channel.protocol.subscriptions,
            as_resolver(None),
            send,
            limit=1,
            server=channel.create_server(),
        )

        def envelope(request_id):
            return RpcRequestEnvelope(
                jsonrpc="2.0", id=request_id, method="session.events.open"
            )

        first = asyncio.create_task(session.handle(envelope(1)))
        try:
            await asyncio.wait_for(sending.wait(), 1)
            await session.handle(envelope(2))
            assert responses[0]["error"]["data"]["code"] == "stream_limit"
            if cancel_handshake:
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                await session.handle(envelope(3))
                assert responses[-1]["result"] == {"streamId": "2"}
            else:
                release.set()
                await first
                assert responses[-1]["result"] == {"streamId": "1"}
        finally:
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            await session.close()


async def test_stream_cancel_is_idempotent_and_preserves_the_connection() -> None:
    channel = RpcChannel("jobs")
    stopped = asyncio.Event()

    @channel.method()
    async def ping() -> str:
        return "pong"

    @channel.stream()
    async def progress() -> AsyncIterator[int]:
        try:
            yield 1
            await asyncio.Event().wait()
        finally:
            stopped.set()

    service = RpcDefinition()
    service.socket("/rpc", channels=[channel])
    async with asyncio.timeout(5), RpcTestClient(service, "/rpc") as client:
        opened = await client.request("jobs.progress.open")
        await client.next_notification()
        assert await client.request("jobs.progress.cancel", opened) is None
        assert await client.request("jobs.progress.cancel", opened) is None
        assert await client.request("jobs.ping") == "pong"
        assert stopped.is_set()
