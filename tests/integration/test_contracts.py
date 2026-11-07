from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from dishka import Provider, Scope, provide
from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.testclient import TestClient

from fastapi_canon import (
    Composition,
    Error,
    ErrorOptions,
    Feature,
    export_contracts,
)
from fastapi_canon.integrations.sse.canon import CanonStreamRouter
from fastapi_canon.integrations.webrtc import Session
from fastapi_canon.integrations.webrtc.router import WebRtcRouter
from fastapi_canon.integrations.websockets import (
    CanonWebSocketRouter,
    Inject,
    ProtocolDefinitionError,
    RpcChannel,
    RpcSocket,
)


def test_export_matches_registered_http_sse_rpc_and_webrtc_paths() -> None:
    channel = RpcChannel("control")

    @channel.method()
    async def ping() -> str:
        return "pong"

    @channel.stream(content_type="image/jpeg")
    async def frames() -> AsyncIterator[bytes]:
        yield b"frame"

    rpc = CanonWebSocketRouter()
    rpc.socket("/control", channels=[channel], name="control")
    rpc.stream("/frames", frames)
    rtc = WebRtcRouter()

    @rtc.session("/webrtc", name="screencast")
    def screencast() -> Session:
        raise AssertionError("Export must not invoke the session factory")

    http = CanonStreamRouter()

    @http.get("/status", operation_id="tunnel.status")
    async def status() -> str:
        return "ok"

    @http.stream("/events", operation_id="tunnel.events")
    async def events() -> AsyncIterator[str]:
        yield "ready"

    parent = APIRouter(prefix="/projects/{projectId}/sessions/{sessionId}")
    parent.include_router(rpc)
    parent.include_router(rtc)
    parent.include_router(http)
    app = FastAPI(title="Browser tunnel")
    app.include_router(parent, prefix="/api/v1")
    prefix = "/api/v1/projects/{projectId}/sessions/{sessionId}"

    contracts = export_contracts(app, base_url="https://api.example.test")

    assert contracts.openapi is not None
    assert (
        contracts.openapi["paths"][prefix + "/status"]["get"]["operationId"]
        == "tunnel.status"
    )
    assert (
        "text/event-stream"
        in contracts.openapi["paths"][prefix + "/events"]["get"]["responses"]["200"][
            "content"
        ]
    )
    assert contracts.openrpc is not None
    server = contracts.openrpc["servers"][0]
    assert server["url"] == "wss://api.example.test" + prefix + "/control"
    assert set(server["variables"]) == {"projectId", "sessionId"}
    assert contracts.openrpc["methods"][0]["servers"] == [server]
    assert (
        contracts.openrpc["x-rpckit-binary-streams"][0]["url"]
        == "wss://api.example.test" + prefix + "/frames"
    )
    assert contracts.webrtc is not None
    assert contracts.webrtc["sessions"][0]["path"] == prefix + "/webrtc"
    with (
        TestClient(app) as client,
        client.websocket_connect("/api/v1/projects/p/sessions/s/control") as socket,
    ):
        socket.send_json({"jsonrpc": "2.0", "id": 1, "method": "control.ping"})
        assert socket.receive_json() == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": "pong",
        }


def test_export_omits_unmounted_endpoints_and_keeps_service_contract_independent() -> (
    None
):
    public = RpcChannel("public")
    private = RpcChannel("private")

    @public.method()
    async def ping() -> str:
        return "pong"

    @private.method()
    async def secret() -> str:
        return "private"

    service = CanonWebSocketRouter()
    selected = service.socket("/rpc", channels=[public], name="public")
    service.socket("/private", channels=[private], name="private")
    routes = CanonWebSocketRouter()
    routes.mount(selected)
    app = FastAPI()
    app.include_router(routes, prefix="/api")

    document = export_contracts(app, kinds=["openrpc"]).openrpc

    assert document is not None
    assert [method["name"] for method in document["methods"]] == ["public.ping"]
    assert document["servers"][0]["url"] == "{baseUrl}/api/rpc"
    standalone = service.contract(
        title="Service", base_url="ws://localhost"
    ).to_openrpc()
    assert {method["name"] for method in standalone["methods"]} == {
        "public.ping",
        "private.secret",
    }


def test_export_reads_custom_handlers_and_mounted_subapplications() -> None:
    channel = RpcChannel("custom")

    @channel.method()
    async def ping() -> None:
        return None

    async def handler(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.close()

    routes = CanonWebSocketRouter()
    routes.socket("/rpc", channels=[channel], name="custom", serve=handler)
    child = FastAPI()
    child.include_router(routes, prefix="/v1")
    app = FastAPI()
    app.mount("/backend", child)

    document = export_contracts(app, kinds=["openrpc"]).openrpc

    assert document is not None
    assert document["servers"][0]["url"] == "{baseUrl}/backend/v1/rpc"


def test_export_rejects_ambiguous_multiple_mounts_with_both_paths() -> None:
    channel = RpcChannel("jobs")

    @channel.method()
    async def ping() -> None:
        return None

    router = CanonWebSocketRouter("/rpc", channels=[channel])
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.include_router(router, prefix="/v2")

    with pytest.raises(ProtocolDefinitionError, match="'/v1/rpc' and '/v2/rpc'"):
        export_contracts(app, kinds=["openrpc"])


def test_export_never_enters_lifespan_and_absent_transports_are_none() -> None:
    class RuntimeProvider(Provider):
        @provide(scope=Scope.APP)
        def secret(self) -> str:
            raise AssertionError("Export must not resolve provider functions")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        raise AssertionError("Runtime must not start during contract export")
        yield

    app = Composition(
        features=[Feature(name="runtime", providers=[RuntimeProvider])]
    ).build(FastAPI(lifespan=lifespan))
    contracts = export_contracts(app)

    assert set(contracts.documents) == {"openapi"}
    assert contracts.openrpc is None
    assert contracts.webrtc is None
    assert app.openapi()["openapi"] == "3.1.0"


def test_export_preserves_raw_sockets_and_composed_error_catalog() -> None:
    class Missing(Exception):
        pass

    missing = Error(Missing, status=404, code="missing", title="Missing")
    channel = RpcChannel("browser")

    @channel.method(raises=[missing])
    async def open() -> None:
        raise Missing()

    @channel.socket()
    async def frames(socket: Inject[RpcSocket]) -> None:
        await socket.close()

    routes = CanonWebSocketRouter()
    routes.socket("/control", channels=[channel], name="control")
    routes.socket("/frames", frames)
    app = Composition(
        features=[Feature(name="browser", routers=[routes])],
        errors=ErrorOptions(type_base="https://api.example.test/problems"),
    ).build(FastAPI())

    document = export_contracts(app, kinds=["openrpc"]).openrpc

    assert document is not None
    assert (
        document["methods"][0]["errors"][0]["x-canon-problem-type"]
        == "https://api.example.test/problems/missing"
    )
    assert document["x-rpckit-sockets"][0]["url"] == "{baseUrl}/frames"
    assert document["x-rpckit-sockets"][0]["name"] == "browser.frames"


@pytest.mark.parametrize("kinds", [[], ["openapi", "openapi"], ["unknown"]])
def test_export_rejects_invalid_selection(kinds: list) -> None:
    with pytest.raises(ValueError, match="unique"):
        export_contracts(FastAPI(), kinds=kinds)
