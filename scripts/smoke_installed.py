import asyncio
import faulthandler
import importlib
import importlib.util
import json
import logging
import runpy
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from importlib.metadata import version
from logging.handlers import BufferingHandler
from pathlib import Path
from typing import Literal

from fastapi import APIRouter as FastAPIRouter
from fastapi import Depends, FastAPI, Header, WebSocket
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from starlette.websockets import WebSocketDisconnect

_MODULES = {
    "fastapi-canon": "fastapi_canon",
    "sse": "fastapi_canon.integrations.sse",
    "websockets": "fastapi_canon.integrations.websockets",
    "webrtc": "fastapi_canon.integrations.webrtc",
}
_STACK = {"fastapi-canon", "sse", "websockets"}


class Event(BaseModel):
    type: Literal["ready"] = "ready"
    value: int = 1


class SocketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class SocketOutput(BaseModel):
    value: int


def _main() -> None:
    selected = set(sys.argv[1:])
    assert importlib.util.find_spec("rpckit") is None
    assert importlib.util.find_spec("fastapi_wsrpc") is None
    for legacy in ("fastapi_streams", "fastapi_rpckit", "fastapi_webrtc"):
        assert importlib.util.find_spec(legacy) is None
    for name, module in _MODULES.items():
        if name in selected:
            installed = importlib.import_module(module)
            assert Path(installed.__file__).resolve().is_relative_to(Path(sys.prefix))
            if hasattr(installed, "__version__"):
                assert installed.__version__ == version("fastapi-canon")
        else:
            assert importlib.util.find_spec(module) is not None, module
    assert not ({"sqlalchemy", "alembic", "aiosqlite", "asyncpg"} & sys.modules.keys())
    app = FastAPI()
    if importlib.util.find_spec("jsonschema") and importlib.util.find_spec("jinja2"):
        _offline_sse_codegen()
        _unified_codegen()
    routers = []
    if "fastapi-canon" in selected:
        from fastapi_canon import APIRouter

        _cli("generate")
        if not (
            importlib.util.find_spec("jsonschema")
            and importlib.util.find_spec("jinja2")
        ):
            _core_generation_diagnostics()
        _cli("db")
        _skill_check()
        _shared_problem_check()
        http = APIRouter()

        @http.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        routers.append(http)
        _canon_check()
        asyncio.run(_scoped_check())
        _request_logging_check()
        _observation_check(enabled="--otel" in selected)
        _resource_telemetry_check(selected)
        _database_check(selected)
        _mcp_check(selected)
    if "sse" in selected:
        from fastapi_canon.integrations.sse.router import StreamRouter

        if "fastapi-canon" in selected:
            from fastapi_canon.integrations.sse.canon import CanonStreamRouter

            streams = CanonStreamRouter()
        else:
            streams = StreamRouter()

        @streams.stream("/events", operation_id="events")
        async def events() -> AsyncIterator[Event]:
            yield Event()

        routers.append(streams)
        _cli("sse")
    if "websockets" in selected:
        from fastapi_canon.integrations.websockets import (
            CanonWebSocketRouter,
            Inject,
            RpcChannel,
            RpcMiddleware,
            RpcSocket,
        )

        channel = RpcChannel("health")

        @channel.method("ping")
        async def ping() -> str:
            return "pong"

        rpc = CanonWebSocketRouter()
        rpc.socket("/rpc", channels=(channel,))

        @channel.socket("messages")
        async def messages(message: SocketInput) -> SocketOutput:
            return SocketOutput(value=message.value + 1)

        rpc.socket("/messages", messages)

        @channel.socket("session")
        async def session(socket: Inject[RpcSocket[SocketInput, SocketOutput]]) -> None:
            await socket.send(SocketOutput(value=0))
            async for message in socket:
                await socket.send(SocketOutput(value=message.value + 1))

        rpc.socket("/session", session)
        routers.append(rpc)

        async def connection_label() -> str:
            return "ready"

        async def custom(socket: WebSocket, label: Inject[str]) -> None:
            await socket.accept()
            await socket.send_text(label)
            await socket.close()

        custom_rpc = CanonWebSocketRouter(
            context=connection_label, middleware=[RpcMiddleware()]
        )
        custom_rpc.socket("/custom", channels=[RpcChannel("custom")], serve=custom)
        routers.append(custom_rpc)
        _cli("websockets")
    if "webrtc" in selected:
        engine = _RecordingEngine()
        routers.append(_webrtc_router(engine, canon="fastapi-canon" in selected))
        _cli("webrtc")
    if "fastapi-canon" in selected:
        from fastapi_canon import Composition, ErrorOptions, Feature

        app = Composition(
            features=(Feature(name="installed", routers=routers),),
            errors=ErrorOptions(type_base="https://example.test/problems"),
        ).build(app)
    else:
        for router in routers:
            app.include_router(router)
    with TestClient(app) as client:
        if "fastapi-canon" in selected:
            assert client.get("/health").json() == {"status": "ok"}
        if "sse" in selected:
            assert '"type":"ready"' in client.get("/events").text
        if "websockets" in selected:
            with client.websocket_connect("/rpc") as websocket:
                websocket.send_json(
                    {"jsonrpc": "2.0", "id": 1, "method": "health.ping"}
                )
                assert websocket.receive_json()["result"] == "pong"
            with client.websocket_connect("/messages") as websocket:
                websocket.send_json({"value": 41})
                assert websocket.receive_json() == {"value": 42}
                websocket.send_json({"value": "secret"})
                try:
                    websocket.receive_json()
                except WebSocketDisconnect as error:
                    assert error.code == 1002
                    assert "secret" not in error.reason
                else:
                    raise AssertionError("Typed socket accepted an invalid message")
            with client.websocket_connect("/session") as websocket:
                assert websocket.receive_json() == {"value": 0}
                websocket.send_json({"value": 41})
                assert websocket.receive_json() == {"value": 42}
            with client.websocket_connect("/custom") as websocket:
                assert websocket.receive_text() == "ready"
        if "webrtc" in selected:
            _webrtc_sessions(client, engine, canon="fastapi-canon" in selected)
    assert app.openapi()["openapi"]
    if "fastapi-canon" in selected:
        _router_policies()
    if "webrtc" in selected:
        _webrtc_codegen(app)
    if "websockets" in selected and importlib.util.find_spec("jinja2"):
        _rpc_codegen(rpc)
    if selected >= _STACK:
        # Run the same composition/lifecycle regression against installed wheels.

        example = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "examples/stack_service.py")
        )
        stack = example["create_app"]()
        with TestClient(stack) as client:
            assert client.post("/counter").json() == {"value": 1}
            assert '"value":1' in client.get("/counter/events").text
            with client.websocket_connect("/rpc") as websocket:
                websocket.send_json(
                    {"jsonrpc": "2.0", "id": 1, "method": "counter.increment"}
                )
                assert websocket.receive_json()["result"] == {"value": 2}
            counter = client.portal.call(
                stack.state.dishka_container.get, example["Counter"]
            )
        assert counter.closed
    print(f"Installed artifact checks passed: {', '.join(sorted(selected))}")


def _mcp_check(selected: set[str]) -> None:
    from fastapi_canon.integrations.mcp import McpOptions

    assert McpOptions().path == "/mcp"
    if "--mcp" not in selected:
        assert "fastmcp" not in sys.modules
        return

    import httpx2
    from dishka import Provider, Scope, provide
    from fastapi_canon import Composition, Feature
    from fastapi_canon.integrations.mcp import Inject, McpMount, McpServer
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from fastmcp.utilities.asgi_transport import StreamingASGITransport

    events: list[str] = []

    class Services(Provider):
        @provide(scope=Scope.REQUEST)
        async def label(self) -> AsyncIterator[str]:
            events.append("open")
            try:
                yield "installed"
            finally:
                events.append("close")

    server = McpServer("Installed")

    @server.tool
    async def echo(value: str, label: Inject[str]) -> str:
        return f"{label}:{value}"

    feature = Feature(
        name="installed-mcp",
        providers=[Services],
        mcp=[McpMount(server=server, namespace="smoke")],
    )
    app = Composition(features=[feature], mcp=McpOptions()).build(FastAPI())

    def factory(**kwargs: object) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=StreamingASGITransport(app), **kwargs)

    async def exercise() -> None:
        transport = StreamableHttpTransport(
            "http://testserver/mcp", httpx_client_factory=factory
        )
        async with (
            asyncio.timeout(10),
            app.router.lifespan_context(app),
            Client(transport, timeout=5) as client,
        ):
            tools = await client.list_tools()
            assert [tool.name for tool in tools] == ["smoke_echo"]
            assert set(tools[0].input_schema["properties"]) == {"value"}
            result = await client.call_tool("smoke_echo", {"value": "ok"})
            assert result.data == "installed:ok"
        assert events == ["open", "close"]

    asyncio.run(exercise())


def _database_check(selected: set[str]) -> None:
    if "--database" not in selected:
        return
    from fastapi_canon.integrations.database import Database

    options = {}
    if "--sqlmodel" in selected:
        from sqlmodel.ext.asyncio.session import AsyncSession

        options["session_class"] = AsyncSession
    declaration = Database(url="sqlite://", **options)
    assert declaration.feature.name == "database"
    if "--sqlite" not in selected:
        return

    async def exercise() -> None:
        from dishka import Scope
        from fastapi_canon import Composition
        from fastapi_canon.integrations.database import AlembicMigrations
        from sqlalchemy import Column, MetaData, String, Table, insert, select
        from sqlalchemy.ext.asyncio import AsyncSession

        with tempfile.TemporaryDirectory(prefix="canon-db-smoke-") as directory:
            path = Path(directory)
            metadata = MetaData()
            table = Table(
                "items", metadata, Column("name", String(80), primary_key=True)
            )
            migrations = AlembicMigrations(
                directory=path / "migrations", metadata=metadata
            )
            database = Database(
                url=f"sqlite:///{path / 'test.db'}", migrations=migrations, **options
            )
            migrations.initialize()
            async with database.open_engine() as engine:
                await migrations.revision(engine, message="initial", autogenerate=True)
                await migrations.upgrade(engine)
            app = Composition(features=[database.feature]).build(FastAPI())
            async with app.router.lifespan_context(app):
                async with app.state.dishka_container(scope=Scope.REQUEST) as scope:
                    session = await scope.get(AsyncSession)
                    await session.execute(insert(table).values(name="persisted"))
                async with app.state.dishka_container(scope=Scope.REQUEST) as scope:
                    session = await scope.get(AsyncSession)
                    assert (await session.scalars(select(table.c.name))).all() == [
                        "persisted"
                    ]
                async with database.open_engine() as engine:
                    await migrations.require_current(engine)
                    await migrations.check(engine)

    asyncio.run(exercise())


def _request_logging_check() -> None:
    """Exercise core-only request records against the actual installed wheel."""
    from fastapi_canon import Composition

    logger = logging.getLogger("fastapi_canon.access")
    previous_level = logger.level
    capture = BufferingHandler(10)
    logger.setLevel(logging.INFO)
    logger.addHandler(capture)
    try:
        app = Composition(request_logging=True).build(FastAPI())
        with TestClient(app) as client:
            assert client.get("/absent?secret=hidden").status_code == 404
        assert len(capture.buffer) == 1
        record = capture.buffer[0]
        assert record.event == "http.request.completed"
        assert record.method == "GET" and record.status == 404
        assert record.route is None and record.duration_ms >= 0
        assert not hasattr(record, "trace_id")
        assert "hidden" not in str(vars(record))
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous_level)
        capture.close()


def _resource_telemetry_check(selected: set[str]) -> None:
    from fastapi_canon.integrations.opentelemetry import Telemetry

    if "--httpx" in selected:
        import httpx
        from fastapi_canon.integrations.opentelemetry.httpx import instrument_httpx

        def application(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        with httpx.Client(
            transport=httpx.WSGITransport(app=application), base_url="http://test"
        ) as client:
            with instrument_httpx(client, Telemetry()):
                assert client.get("/").text == "ok"
            assert not client.is_closed
            assert client.get("/").status_code == 200
    if "--sqlalchemy" in selected:
        from fastapi_canon.integrations.opentelemetry.sqlalchemy import (
            instrument_sqlalchemy,
        )
        from sqlalchemy import create_engine, text

        engine = create_engine("sqlite://")
        try:
            with (
                instrument_sqlalchemy(engine, Telemetry()),
                engine.connect() as connection,
            ):
                assert connection.scalar(text("select 1")) == 1
            with engine.connect() as connection:
                assert connection.scalar(text("select 2")) == 2
        finally:
            engine.dispose()


def _observation_check(*, enabled: bool) -> None:
    """Exercise installed decorators with no optional SDK or source overrides."""
    from dishka import FromDishka, Provider, Scope, provide
    from dishka.integrations.fastapi import inject
    from fastapi_canon import APIRouter, Composition, Feature
    from fastapi_canon.integrations.opentelemetry import Telemetry, instrument

    global_provider = None
    if enabled:
        from opentelemetry import trace

        assert importlib.util.find_spec("opentelemetry.sdk") is None
        global_provider = trace.get_tracer_provider()

    class Port:
        async def ping(self) -> str:
            return "pong"

    class Dependencies(Provider):
        port = provide(Port, scope=Scope.REQUEST)

    router = APIRouter()

    @router.get("/observed")
    @inject
    async def observed(port: FromDishka[Port]) -> str:
        return await port.ping()

    app = Composition(
        features=(
            Feature(
                name="observed",
                providers=[Dependencies],
                routers=[router],
                instrumentation=instrument(Port),
            ),
        ),
        telemetry=Telemetry() if enabled else None,
    ).build(FastAPI())
    with TestClient(app) as client:
        assert client.get("/observed").json() == "pong"
    if enabled:
        assert trace.get_tracer_provider() is global_provider
        assert app._telemetry["auto_configure"] is False
    else:
        assert "fastapi_canon.integrations.opentelemetry.ports" not in sys.modules


def _canon_check() -> None:
    with tempfile.TemporaryDirectory(prefix="canon-check-") as temporary:
        root = Path(temporary)
        source = root / "api.py"
        source.write_text(
            "from fastapi_canon import APIRouter, Error, Feature\n"
            "class Missing(Exception): pass\n"
            "missing = Error(Missing, status=404, code='missing', title='Missing')\n"
            "router = APIRouter()\n"
            "feature = Feature(name='api', routers=[router], errors=[missing])\n"
            "@router.get('/')\n"
            "def endpoint(): raise Missing()\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "fastapi_canon.check_error_contracts",
                str(root),
                "--strict",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 1, result.stderr
        report = json.loads(result.stdout)
        assert report["contract_gaps"] == 1
        assert report["incomplete_operations"] == 0
        source.write_text(
            source.read_text("utf-8").replace(
                "@router.get('/')", "@router.get('/', raises=[missing])"
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "fastapi_canon.check_error_contracts",
                str(root),
                "--strict",
                "--require-complete",
            ],
            check=True,
            timeout=30,
        )


def _skill_check() -> None:
    with tempfile.TemporaryDirectory(prefix="canon-skill-") as temporary:
        root = Path(temporary)
        command = [sys.executable, "-I", "-m", "fastapi_canon", "skill", "install"]
        subprocess.run(command, cwd=root, check=True, capture_output=True, timeout=30)
        agents = root / ".agents/skills/build-canon-backend"
        claude = root / ".claude/skills/build-canon-backend"
        expected = {
            path.relative_to(agents): path.read_bytes()
            for path in agents.rglob("*")
            if path.is_file()
        }
        assert Path("SKILL.md") in expected
        assert Path("references/components.md") in expected
        (agents / "references/components.md").unlink()
        (claude / "obsolete.md").write_text("Remove on refresh", encoding="utf-8")
        subprocess.run(
            [*command, "--force"],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=30,
        )
        for directory in (agents, claude):
            assert {
                path.relative_to(directory): path.read_bytes()
                for path in directory.rglob("*")
                if path.is_file()
            } == expected


def _cli(name: str) -> None:
    executable = Path(sys.executable).with_name(
        "fastapi-canon.exe" if sys.platform == "win32" else "fastapi-canon"
    )
    subprocess.run(
        [str(executable), name, "--help"], check=True, capture_output=True, timeout=30
    )


def _core_generation_diagnostics() -> None:
    with tempfile.TemporaryDirectory(prefix="canon-core-generation-") as temporary:
        root = Path(temporary)
        (root / "minimal_app.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n"
            "@app.get('/health')\ndef health() -> str: return 'ok'\n",
            encoding="utf-8",
        )
        (root / "fastapi-canon.toml").write_text(
            '[codegen]\nversion = 1\n[codegen.sources.api]\nkind = "openapi"\n'
            'source = "minimal_app:app"\noutput = "contracts/api.json"\n'
            '[codegen.targets.python]\nlanguage = "python"\nsources = ["api"]\n'
            'output = "sdk"\n',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "fastapi_canon",
                "generate",
                "--format",
                "json",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 2, result.stderr
        report = json.loads(result.stdout)
        assert report["schema_version"] == 1 and report["status"] == "error"
        (diagnostic,) = report["diagnostics"]
        assert diagnostic["code"] == "canon.codegen.E001"
        assert "fastapi-canon[codegen]" in diagnostic["message"]
        assert not (root / "contracts").exists() and not (root / "sdk").exists()


def _offline_sse_codegen() -> None:
    """Generate from a saved contract, including with codegen-only dependencies."""
    contract = (
        Path(__file__).resolve().parents[1] / "tests/fixtures/sse/models.openapi.json"
    )
    with tempfile.TemporaryDirectory(prefix="canon-offline-") as temporary:
        for language in ("python", "typescript"):
            command = [
                sys.executable,
                "-I",
                "-m",
                "fastapi_canon",
                "sse",
                "generate",
                "--schema",
                str(contract),
                "--language",
                language,
                "--output",
                str(Path(temporary) / language),
            ]
            subprocess.run(command, cwd=temporary, check=True, timeout=30)
            subprocess.run([*command, "--check"], cwd=temporary, check=True, timeout=30)


def _unified_codegen() -> None:
    import httpx

    document = {
        "openapi": "3.1.0",
        "info": {"title": "Health", "version": "1"},
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health.get",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {"schema": {"type": "string"}}
                            },
                        }
                    },
                }
            }
        },
    }
    with tempfile.TemporaryDirectory(prefix="canon-unified-") as temporary:
        root = Path(temporary)
        (root / "openapi.json").write_text(json.dumps(document), encoding="utf-8")
        (root / "fastapi-canon.toml").write_text(
            """[codegen]
version = 1
[codegen.sources.api]
kind = "openapi"
source = "must_not_import:app"
output = "openapi.json"
[codegen.targets.python]
language = "python"
sources = ["api"]
output = "artifact_sdk"
package = "artifact_sdk"
[codegen.targets.browser]
language = "typescript"
sources = ["api"]
output = "browser"
""",
            encoding="utf-8",
        )
        command = [
            str(
                Path(sys.executable).with_name(
                    "fastapi-canon.exe" if sys.platform == "win32" else "fastapi-canon"
                )
            ),
            "generate",
            "--offline",
        ]
        subprocess.run(command, cwd=temporary, check=True, timeout=30)
        subprocess.run([*command, "--check"], cwd=temporary, check=True, timeout=30)
        sys.path.insert(0, temporary)
        try:
            sdk = importlib.import_module("artifact_sdk")

            async def consume() -> None:
                transport = httpx.MockTransport(
                    lambda request: httpx.Response(200, json="ok")
                )
                async with httpx.AsyncClient(transport=transport) as http:
                    async with sdk.ApiClient(
                        "https://test", http_client=http
                    ) as client:
                        assert await client.health.get() == "ok"
                    assert not http.is_closed

            asyncio.run(consume())
        finally:
            sys.path.remove(temporary)
            for name in tuple(sys.modules):
                if name == "artifact_sdk" or name.startswith("artifact_sdk."):
                    del sys.modules[name]


class _RecordingEngine:
    """Counts peers; admission must reject sessions before reaching it."""

    def __init__(self) -> None:
        from fastapi_canon.integrations.webrtc.engines import EngineCapabilities

        self.capabilities = EngineCapabilities(
            trickle_ice=True, ice_restart=False, renegotiation=False
        )
        self.created = 0

    def track_kind(self, track):
        raise TypeError("No local tracks in the installed-artifact check")

    async def create_peer(self, config, observer):
        self.created += 1
        raise RuntimeError("The smoke engine allocates no peers")


def _webrtc_router(engine, *, canon=False):
    from fastapi import HTTPException, Query
    from fastapi_canon.integrations.webrtc import DataChannelSpec, Session
    from fastapi_canon.integrations.webrtc.router import WebRtcRouter

    class AdmissionDenied(Exception):
        pass

    declarations = {}
    if canon:
        from fastapi_canon import Error
        from fastapi_canon.integrations.webrtc.canon import CanonWebRtcRouter

        denied = Error(
            AdmissionDenied,
            status=403,
            code="admission_denied",
            title="Denied",
            type="https://example.test/problems/admission_denied",
        )
        rtc = CanonWebRtcRouter(engine=engine, prefix="/rtc")
        declarations["raises"] = [denied]
    else:
        rtc = WebRtcRouter(engine=engine, prefix="/rtc")

    @rtc.session(
        "/rooms/{room}",
        data_channels=(DataChannelSpec(name="events", send_model=SocketOutput),),
        **declarations,
    )
    def room(room: str, token: str = Query()) -> Session:
        if token != "ok":
            if canon:
                raise AdmissionDenied()
            raise HTTPException(status_code=403)
        return Session()

    return rtc


def _webrtc_sessions(client, engine, *, canon=False) -> None:
    from fastapi_canon.integrations.webrtc import SUBPROTOCOL
    from starlette.testclient import WebSocketDenialResponse

    try:
        with client.websocket_connect("/rtc/rooms/a?token=no", [SUBPROTOCOL]):
            raise AssertionError("A denied session was accepted")
    except WebSocketDenialResponse as error:
        assert error.status_code == 403
        if canon:
            assert error.json()["code"] == "admission_denied"
    assert engine.created == 0
    with client.websocket_connect("/rtc/rooms/a?token=ok", [SUBPROTOCOL]) as socket:
        try:
            socket.receive_text()
        except WebSocketDisconnect as error:
            assert (error.code, error.reason) == (1011, "engine_failure")
        else:
            raise AssertionError("Expected an engine_failure close")
    assert engine.created == 1
    if importlib.util.find_spec("aiortc") is None:
        try:
            importlib.import_module("fastapi_canon.integrations.webrtc.engines.aiortc")
        except ImportError as error:
            assert "fastapi-canon[webrtc,aiortc]" in str(error)
        else:
            raise AssertionError("The aiortc engine imported without aiortc")
    else:
        from fastapi_canon.integrations.webrtc.engines.aiortc import AiortcEngine

        assert AiortcEngine().capabilities.renegotiation


def _webrtc_codegen(app) -> None:
    from fastapi_canon.integrations.webrtc.codegen.typescript import generate_typescript
    from fastapi_canon.integrations.webrtc.codegen.writer import write_files
    from fastapi_canon.integrations.webrtc.contract import build_contract

    contract = build_contract(app)
    (session,) = contract["sessions"]
    assert session["path"] == "/rtc/rooms/{room}"
    files = generate_typescript(contract)
    assert "export class RtcClient {" in files["client.ts"]
    assert "readonly room: SessionApi<" in files["client.ts"]
    with tempfile.TemporaryDirectory(prefix="canon-webrtc-client-") as temporary:
        output = Path(temporary) / "client"
        assert write_files(output, files)
        assert not write_files(output, files, check=True)


def _rpc_codegen(rpc) -> None:
    from fastapi_canon.integrations.websockets.codegen import (
        PythonClientOptions,
        TypeScriptClientOptions,
        generate_python_client,
        generate_typescript_client,
    )
    from fastapi_canon.integrations.websockets.testing import InMemorySocketFactory

    document = rpc.contract(title="Health", base_url="ws://test").to_openrpc()
    with tempfile.TemporaryDirectory(prefix="canon-rpc-client-") as temporary:
        working = Path(temporary)
        python_options = PythonClientOptions(
            package="health_client",
            client_name="HealthClient",
            with_transport="websocket",
        )
        typescript_options = TypeScriptClientOptions(with_transport="websocket")
        generate_python_client(document, working / "health_client", python_options)
        generate_typescript_client(document, working / "typescript", typescript_options)
        assert not generate_python_client(
            document, working / "health_client", python_options, check=True
        )
        assert not generate_typescript_client(
            document, working / "typescript", typescript_options, check=True
        )
        sys.path.insert(0, temporary)
        try:
            generated = importlib.import_module("health_client")

            async def consume() -> None:
                async with (
                    InMemorySocketFactory(rpc) as factory,
                    generated.HealthClient.connect(
                        socket_factory=factory, socket_connection_factory=factory
                    ) as client,
                ):
                    assert await client.health.ping() == "pong"
                    async with client.health.messages() as socket:
                        await socket.send(generated.SocketInput(value=41))
                        result = await socket.receive()
                        assert isinstance(result, generated.SocketOutput)
                        assert result.value == 42
                    async with client.health.session() as socket:
                        assert (await socket.receive()).value == 0
                        await socket.send(generated.SocketInput(value=41))
                        assert (await socket.receive()).value == 42

            asyncio.run(consume())
        finally:
            sys.path.remove(temporary)
        node = shutil.which("node")
        compiler = (
            Path(__file__).resolve().parents[1] / "node_modules/typescript/bin/tsc"
        )
        assert node and compiler.is_file(), (
            "Run pnpm install --frozen-lockfile --ignore-scripts first"
        )
        subprocess.run(
            [
                node,
                str(compiler),
                "--strict",
                "--target",
                "ES2022",
                "--module",
                "ESNext",
                "--moduleResolution",
                "Bundler",
                "--lib",
                "ES2022,DOM,ESNext.Disposable",
                "--skipLibCheck",
                "--noEmit",
                "typescript/index.ts",
            ],
            cwd=working,
            check=True,
            timeout=30,
        )


def _router_policies() -> None:
    """Check the public policy API against installed wheels, including minima."""
    from fastapi_canon import (
        APIRouter,
        Composition,
        Error,
        ErrorOptions,
        Feature,
        RouterPolicy,
    )

    class AuthenticationRequired(Exception):
        pass

    class ItemDenied(Exception):
        pass

    authentication_required = Error(
        AuthenticationRequired,
        status=401,
        code="authentication_required",
        title="Authentication required",
    )
    item_denied = Error(ItemDenied, status=401, code="item_denied", title="Item denied")
    problems = ErrorOptions(type_base="https://example.test/problems")

    async def require_user(authorization: str | None = Header(default=None)) -> None:
        if authorization != "Bearer test":
            raise AuthenticationRequired

    policy = RouterPolicy(
        dependencies=[Depends(require_user)],
        raises=[authentication_required],
    )
    for direct in (False, True):
        router = APIRouter(policy=policy if direct else None)
        router.get("/policy", raises=[item_denied])(lambda: "ok")
        feature = Feature(name="policy", routers=[router])
        composition = Composition(
            features=(feature,),
            errors=problems,
            router_factory=None if direct else lambda: APIRouter(policy=policy),
        )
        app = composition.build(FastAPI())
        with TestClient(app) as client:
            assert client.get("/policy").json()["code"] == "authentication_required"
            assert (
                client.get("/policy", headers={"Authorization": "Bearer test"}).json()
                == "ok"
            )
        response = app.openapi()["paths"]["/policy"]["get"]["responses"]["401"]
        assert (
            len(response["content"]["application/problem+json"]["schema"]["oneOf"]) == 2
        )
        if not direct:
            public = Composition(features=(feature,), errors=problems).build(FastAPI())
            assert TestClient(public).get("/policy").status_code == 200


async def _scoped_check() -> None:
    from dishka import Provider, Scope, make_async_container, provide
    from fastapi_canon import Scoped

    events: list[str] = []

    class Resources(Provider):
        @provide(scope=Scope.REQUEST)
        async def number(self) -> AsyncIterator[int]:
            events.append("open")
            try:
                yield 40
            finally:
                events.append("close")

    class Add:
        def __init__(self, number: int) -> None:
            self._number = number

        async def __call__(self, *, increment: int) -> int:
            return self._number + increment

    provider = Resources()
    provider.provide(Add, scope=Scope.REQUEST)
    async with asyncio.timeout(5), make_async_container(provider) as container:
        add = Scoped(container, Add)
        assert await add(increment=2) == 42
        assert events == ["open", "close"]
        assert await add(increment=3) == 43
        assert events == ["open", "close", "open", "close"]


def _shared_problem_check() -> None:
    from fastapi_canon import APIRouter, Composition, Error, ErrorOptions, Feature

    class Missing(Exception):
        pass

    missing = Error(
        Missing,
        status=404,
        code="item_missing",
        title="Item missing",
        extensions={"replacement": None},
    )
    router = APIRouter()

    @router.get("/missing", raises=[missing])
    async def fail() -> None:
        raise Missing("private")

    parent = FastAPIRouter()
    parent.include_router(router)
    app = Composition(
        features=(Feature(name="missing", routers=[parent]),),
        errors=ErrorOptions(type_base="urn:canon:smoke"),
    ).build(FastAPI())
    with TestClient(app) as client:
        response = client.get("/missing")
    payload = response.json()
    assert response.status_code == payload["status"] == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert payload["replacement"] is None
    assert "private" not in response.text
    schema = app.openapi()["components"]["schemas"]["ItemMissingProblem"]
    assert schema["properties"]["type"]["const"] == payload["type"]


if __name__ == "__main__":
    faulthandler.dump_traceback_later(90, exit=True)
    try:
        _main()
    finally:
        faulthandler.cancel_dump_traceback_later()
