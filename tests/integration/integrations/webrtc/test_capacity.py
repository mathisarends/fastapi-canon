"""Share one session capacity between declarations on different routers."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_canon import Composition, Feature
from fastapi_canon.integrations.webrtc import (
    ErrorCode,
    Session,
    SessionCapacity,
    SessionEvent,
    SessionEventKind,
    WebRtcRouter,
)
from fastapi_canon.integrations.webrtc.engines.aiortc import AiortcEngine


def test_shared_capacity_refuses_until_the_open_session_closes() -> None:
    capacity = SessionCapacity(max_sessions=1)
    events: list[SessionEvent] = []
    engine = AiortcEngine()
    voice = WebRtcRouter(prefix="/voice", engine=engine, on_event=events.append)
    video = WebRtcRouter(prefix="/video", engine=engine, on_event=events.append)

    @voice.session("/rtc", capacity=capacity)
    def talk() -> Session:
        return Session()

    @video.session("/rtc", capacity=capacity)
    def watch() -> Session:
        return Session()

    app = Composition(features=(Feature(name="media", routers=[voice, video]),)).build(
        FastAPI()
    )
    with TestClient(app) as client:
        with client.websocket_connect("/voice/rtc") as first:
            assert first.receive_json()["type"] == "session"
            assert capacity.active == 1
            with client.websocket_connect("/video/rtc") as refused:
                assert refused.receive()["reason"] == "session_limit"
        assert capacity.active == 0
        with client.websocket_connect("/video/rtc") as admitted:
            assert admitted.receive_json()["type"] == "session"
            assert capacity.active == 1
        assert capacity.active == 0

    rejected = [e for e in events if e.kind == SessionEventKind.REJECTED]
    assert [(e.name, e.code) for e in rejected] == [("watch", ErrorCode.SESSION_LIMIT)]
