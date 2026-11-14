"""Automatic factory injection through real bounded signaling sessions."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Annotated

import pytest
from aiortc import AudioStreamTrack
from dishka import FromDishka, Provider, Scope, provide
from fastapi import FastAPI, Header, HTTPException
from fastapi_canon import APIRouter, Composition, Feature, Inject
from fastapi_canon.integrations.webrtc import ErrorCode, Peer, Session, WebRtcRouter
from fastapi_canon.integrations.webrtc.contract import build_contract
from fastapi_canon.integrations.webrtc.engines.aiortc import AiortcEngine


class Resource:
    def __init__(self, events: list[str]) -> None:
        self.track = AudioStreamTrack()
        self.events = events
        self.closed = False


@pytest.mark.parametrize(
    "kind", ["coroutine", "function", "async-generator", "generator"]
)
@pytest.mark.asyncio
async def test_factory_forms_share_session_resources_until_after_cleanup(
    kind: str,
) -> None:
    events: list[str] = []
    resources: list[Resource] = []

    class Services(Provider):
        @provide(scope=Scope.SESSION)
        async def resource(self) -> AsyncIterator[Resource]:
            resource = Resource(events)
            resources.append(resource)
            events.append("open")
            try:
                yield resource
            finally:
                events.append(f"dependency_close:{resource.track.readyState}")
                resource.closed = True
                resource.track.stop()

    def session(
        item_id: int, token: str, resource: Resource, same: Resource
    ) -> Session:
        assert same is resource
        assert not resource.closed
        if token != "ok":
            events.append("denied")
            raise HTTPException(status_code=403)
        events.append(f"factory:{item_id}")

        async def on_close(peer: Peer, code: ErrorCode) -> None:
            assert not resource.closed
            events.append(f"on_close:{resource.track.readyState}")

        return Session(tracks=[resource.track], on_close=on_close)

    async def coroutine(
        item_id: int,
        resource: Inject[Resource],
        same: FromDishka[Resource],
        token: Annotated[str, Header()],
    ) -> Session:
        return session(item_id, token, resource, same)

    def function(
        item_id: int,
        resource: Inject[Resource],
        same: FromDishka[Resource],
        token: Annotated[str, Header()],
    ) -> Session:
        return session(item_id, token, resource, same)

    async def async_generator(
        item_id: int,
        resource: Inject[Resource],
        same: FromDishka[Resource],
        token: Annotated[str, Header()],
    ) -> AsyncIterator[Session]:
        try:
            yield session(item_id, token, resource, same)
        finally:
            assert not resource.closed
            events.append(f"factory_close:{resource.track.readyState}")

    def generator(
        item_id: int,
        resource: Inject[Resource],
        same: FromDishka[Resource],
        token: Annotated[str, Header()],
    ) -> Iterator[Session]:
        try:
            yield session(item_id, token, resource, same)
        finally:
            assert not resource.closed
            events.append(f"factory_close:{resource.track.readyState}")

    factories = {
        "coroutine": coroutine,
        "function": function,
        "async-generator": async_generator,
        "generator": generator,
    }
    factory = factories[kind]
    original = dict(vars(factory))
    router = WebRtcRouter(prefix="/items", engine=AiortcEngine())
    router.session("/{item_id}/media", name="media")(factory)
    parent = APIRouter(prefix="/api")
    parent.include_router(router)
    composition = Composition(
        features=[Feature(name="media", routers=[parent], providers=[Services()])]
    )
    app = FastAPI()
    report = composition.check(app)
    assert report.complete and not report.has_errors
    composition.build(app)
    assert resources == events == []
    assert vars(factory) == original
    contract = build_contract(app)["sessions"][0]
    assert contract["path"] == "/api/items/{item_id}/media"
    assert [(p["name"], p["in"], p["required"]) for p in contract["parameters"]] == [
        ("item_id", "path", True)
    ]

    async def connect(token: str, *, accepted: bool) -> None:
        inbound: asyncio.Queue = asyncio.Queue()
        outbound: asyncio.Queue = asyncio.Queue()
        inbound.put_nowait({"type": "websocket.connect"})
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": "/api/items/7/media",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"token", token.encode())],
            "subprotocols": [],
            "state": {},
            "extensions": {"websocket.http.response": {}},
        }
        task = asyncio.create_task(app(scope, inbound.get, outbound.put))
        try:
            first = await outbound.get()
            if accepted:
                assert first["type"] == "websocket.accept"
                assert json.loads((await outbound.get())["text"])["type"] == "session"
                assert not resources[-1].closed
                inbound.put_nowait({"type": "websocket.disconnect", "code": 1000})
            else:
                assert first["type"] == "websocket.http.response.start"
                assert first["status"] == 403
            await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async with asyncio.timeout(10), app.router.lifespan_context(app):
        await connect("ok", accepted=True)
        assert resources[0].closed
        expected = ["open", "factory:7", "on_close:live"]
        if "generator" in kind:
            expected.append("factory_close:ended")
        assert events == [*expected, "dependency_close:ended"]
        events.clear()
        await connect("denied", accepted=False)
        assert resources[1].closed
        assert "on_close:live" not in events
        assert events[0:2] == ["open", "denied"]
        assert events[-1] == "dependency_close:live"


@pytest.mark.parametrize("binding", [None, Scope.APP, Scope.SESSION, Scope.REQUEST])
def test_factory_preflight_checks_session_scope_without_resolving_services(
    binding: Scope | None,
) -> None:
    calls: list[str] = []
    providers: list[Provider] = []
    if binding is not None:
        provider = Provider()

        def resource() -> Resource:
            calls.append("provider")
            return Resource([])

        provider.provide(resource, scope=binding)
        providers.append(provider)
    router = WebRtcRouter(prefix="/items")

    @router.session("/{item_id}/media")
    async def media(item_id: int, resource: Inject[Resource]) -> Session:
        calls.append("factory")
        return Session()

    composition = Composition(
        features=[Feature(name="media", routers=[router], providers=providers)]
    )
    app = FastAPI()
    report = composition.check(app)
    assert calls == []
    failures = [
        issue for issue in report.issues if issue.code == "canon.providers.E004"
    ]
    if binding in (Scope.APP, Scope.SESSION):
        assert report.complete and not report.has_errors
        composition.build(app)
        assert calls == []
    else:
        assert len(failures) == 1
        assert failures[0].feature_names == ("media",)
        assert failures[0].locations[0] == "WEBSOCKET /items/{item_id}/media"
        with pytest.raises(ValueError, match="APP/SESSION"):
            composition.build(app)
        assert not hasattr(app.state, "dishka_container")
        assert not any(route.path == "/items/{item_id}/media" for route in app.routes)
        assert calls == []
