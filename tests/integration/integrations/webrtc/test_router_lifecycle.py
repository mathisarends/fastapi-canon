"""WebRTC router sessions through a real engine and the application lifespan."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi_canon import Composition, Feature
from fastapi_canon.integrations.webrtc import SUBPROTOCOL, Session, WebRtcRouter
from fastapi_canon.integrations.webrtc.engines.aiortc import AiortcEngine
from fastapi_canon.integrations.webrtc.events import SessionEvent, SessionEventKind
from starlette.types import Message


def test_webrtc_uses_real_engine_and_closes_factory_dependencies() -> None:
    lifecycle: list[str] = []
    observed: list[SessionEvent] = []
    router = WebRtcRouter(
        prefix="/projects", engine=AiortcEngine(), on_event=observed.append
    )

    @router.session("/{project_id}/media")
    async def media(project_id: str) -> AsyncIterator[Session]:
        lifecycle.append(f"open {project_id}")
        try:
            yield Session()
        finally:
            lifecycle.append(f"close {project_id}")

    app = Composition(features=[Feature(name="projects", routers=[router])]).build(
        FastAPI()
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            _connection(app, "/projects/42/media", [SUBPROTOCOL]) as (
                _incoming,
                outgoing,
            ),
        ):
            message = await _receive(outgoing)
            assert message["type"] == "session"
            assert message["name"] == "media"
            assert lifecycle == ["open 42"]
        assert lifecycle == ["open 42", "close 42"]
        assert [event.kind for event in observed] == [
            SessionEventKind.ADMITTED,
            SessionEventKind.CLOSED,
        ]

    asyncio.run(scenario())
    assert lifecycle == ["open 42", "close 42"]


def test_application_shutdown_closes_active_webrtc_sessions() -> None:
    closed: list[str] = []
    router = WebRtcRouter(engine=AiortcEngine())

    @router.session("/media")
    async def media() -> AsyncIterator[Session]:
        try:
            yield Session()
        finally:
            closed.append("factory closed")

    app = Composition(features=[Feature(name="media", routers=[router])]).build(
        FastAPI()
    )

    async def scenario() -> Message:
        async with AsyncExitStack() as connections:
            async with app.router.lifespan_context(app):
                _incoming, outgoing = await connections.enter_async_context(
                    _connection(app, "/media", [SUBPROTOCOL])
                )
                assert (await _receive(outgoing))["type"] == "session"
            message = await outgoing.get()
            return message

    assert asyncio.run(scenario()) == {
        "type": "websocket.close",
        "code": 1001,
        "reason": "shutdown",
    }
    assert closed == ["factory closed"]


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
