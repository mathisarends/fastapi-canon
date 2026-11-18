"""WebRTC router sessions through a real engine and the application lifespan."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from fastapi_canon import Composition, Feature
from fastapi_canon.integrations.webrtc import (
    SUBPROTOCOL,
    Session,
    SessionEvent,
    SessionEventKind,
    WebRtcRouter,
)
from fastapi_canon.integrations.webrtc.engines.aiortc import AiortcEngine
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


@pytest.mark.parametrize("custom_lifespan", [False, True])
def test_application_shutdown_closes_active_webrtc_sessions(
    custom_lifespan: bool,
) -> None:
    closed: list[str] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[dict[str, str]]:
        closed.append("lifespan open")
        try:
            yield {"media": "ready"}
        finally:
            assert router.active_sessions == 0
            closed.append("lifespan close")

    router = WebRtcRouter(
        engine=AiortcEngine(), lifespan=lifespan if custom_lifespan else None
    )

    @asynccontextmanager
    async def resources(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            assert router.active_sessions == 0
            closed.append("resources closed")

    @router.session("/media")
    async def media() -> AsyncIterator[Session]:
        try:
            yield Session()
        finally:
            closed.append("factory closed")

    app = Composition(
        features=[Feature(name="media", routers=[router], lifespan=resources)]
    ).build(FastAPI())

    async def scenario() -> Message:
        async with AsyncExitStack() as connections:
            async with app.router.lifespan_context(app) as state:
                assert state == ({"media": "ready"} if custom_lifespan else {})
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
    assert closed == (
        ["lifespan open", "factory closed", "lifespan close", "resources closed"]
        if custom_lifespan
        else ["factory closed", "resources closed"]
    )


def test_http_router_options_are_forwarded() -> None:
    router = WebRtcRouter(
        prefix="/rtc",
        redirect_slashes=False,
        default_response_class=PlainTextResponse,
        responses={404: {"description": "Missing media"}},
        tags=["media"],
        deprecated=True,
    )

    @router.get("/health")
    async def health() -> str:
        return "ready"

    app = Composition(features=[Feature(name="media", routers=[router])]).build(
        FastAPI()
    )
    with TestClient(app) as client:
        response = client.get("/rtc/health")
    assert response.status_code == 200
    assert response.text == "ready"
    assert response.headers["content-type"].startswith("text/plain")
    operation = app.openapi()["paths"]["/rtc/health"]["get"]
    assert operation["responses"]["404"]["description"] == "Missing media"
    assert operation["tags"] == ["media"]
    assert operation["deprecated"] is True
    assert router.redirect_slashes is False


@pytest.mark.parametrize("custom_app_lifespan", [False, True])
def test_router_event_handlers_run_once(custom_app_lifespan: bool) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    router = WebRtcRouter(
        on_startup=[lambda: events.append("startup")],
        on_shutdown=[lambda: events.append("shutdown")],
    )
    app = Composition(features=[Feature(name="media", routers=[router])]).build(
        FastAPI(lifespan=lifespan) if custom_app_lifespan else FastAPI()
    )
    with TestClient(app):
        assert events == ["startup"]
    assert events == ["startup", "shutdown"]


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
