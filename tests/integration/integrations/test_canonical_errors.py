import asyncio
import importlib
import json
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError

from examples.canonical_errors.app import create_app, project_missing
from fastapi_canon import (
    Composition,
    ErrorOptions,
    Feature,
    export_contracts,
)
from fastapi_canon.error import ErrorRegistry
from fastapi_canon.integrations.sse.codegen.ir import build_ir as stream_ir
from fastapi_canon.integrations.sse.codegen.openapi import export_schema
from fastapi_canon.integrations.sse.codegen.python import generate_python
from fastapi_canon.integrations.websockets import (
    CanonWebSocketRouter,
    Inject,
    RpcChannel,
)
from fastapi_canon.integrations.websockets.codegen import (
    PythonClientOptions,
    render_python_client,
)


def test_empty_catalog_keeps_its_configured_internal_problem_identity() -> None:
    from fastapi import FastAPI

    from examples.canonical_errors.app import ProjectEvent
    from fastapi_canon.integrations.sse.canon import CanonStreamRouter

    router = CanonStreamRouter()

    @router.stream("/events")
    async def events() -> AsyncIterator[ProjectEvent]:
        yield ProjectEvent(sequence=1)
        raise ValueError("private")

    app = FastAPI()
    app.include_router(router)
    ErrorRegistry(errors=[], type_base="urn:empty:problems").install(app)
    response = TestClient(app).get("/events")
    assert "urn:empty:problems/internal_server_error" in response.text
    assert "private" not in response.text


def test_same_domain_error_across_http_stream_and_rpc() -> None:
    app = create_app()
    with TestClient(app) as client:
        http_problem = client.get("/projects/0").json()
        admission = client.get("/projects/0/events")
        terminal = client.get("/projects/2/events")
        frame = terminal.text.split("event: canon.problem\n", 1)[1]
        stream_problem = json.loads(frame.split("data: ", 1)[1])
        with client.websocket_connect("/ws") as socket:
            socket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "projects.cancel",
                    "params": {"project_id": 0},
                }
            )
            response = socket.receive_json()
    rpc_problem = response["error"]["data"]["problem"]
    assert response["error"]["code"] == -32000
    assert response["error"]["message"] == "Project not found"
    assert response["error"]["data"] == {"problem": stream_problem}
    assert (
        {k: v for k, v in http_problem.items() if k != "status"}
        == stream_problem
        == rpc_problem
    )
    assert http_problem["status"] == 404
    assert admission.status_code == 404
    assert admission.json() == http_problem
    assert "status" not in stream_problem
    assert "private" not in terminal.text
    assert "canon.complete" not in terminal.text
    assert stream_problem["replacement"] is None
    schema = _example_openrpc()["components"]["schemas"]["ProjectMissingProblem"]
    Draft202012Validator(schema).validate(rpc_problem)
    operation = app.openapi()["paths"]["/projects/{project_id}/events"]["get"]
    assert operation["x-canon-stream"]["version"] == 1
    assert "errorIds" not in operation["x-canon-stream"]


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("before_first_yield", [False, True])
def test_sse_raises_registers_shared_and_local_processing_errors(
    shared: bool, before_first_yield: bool
) -> None:
    from fastapi import FastAPI

    from examples.canonical_errors.app import ProjectMissing
    from fastapi_canon.integrations.sse.canon import CanonStreamRouter

    router = CanonStreamRouter(raises=[project_missing] if shared else [])

    @router.stream("/events", raises=[] if shared else [project_missing])
    async def events() -> AsyncIterator[int]:
        if not before_first_yield:
            yield 1
        raise ProjectMissing("private failure")

    app = Composition(
        features=[Feature(name="events", routers=[router])],
        errors=ErrorOptions(type_base="urn:stream:problem"),
    ).build(FastAPI())
    with TestClient(app) as client:
        response = client.get("/events")
    assert response.status_code == 200
    assert "event: canon.problem" in response.text
    assert "urn:stream:problem/project_missing" in response.text
    assert "private failure" not in response.text
    assert "canon.complete" not in response.text
    operation = app.openapi()["paths"]["/events"]["get"]
    assert "404" in operation["responses"]
    assert len(operation["x-canon-stream"]["problems"]) == 1


def test_nested_plain_router_keeps_declarations_isolated_between_applications() -> None:
    from fastapi import APIRouter, FastAPI

    from examples.canonical_errors.app import router

    parent = APIRouter(prefix="/nested")
    parent.include_router(router)
    feature = Feature(name="projects", routers=[parent])
    first = Composition(
        features=[feature], errors=ErrorOptions(type_base="urn:first:problem")
    ).build(FastAPI())
    second = Composition(
        features=[feature], errors=ErrorOptions(type_base="urn:second:problem")
    ).build(FastAPI())
    with TestClient(first) as client:
        response = client.get("/nested/projects/0")
        terminal = client.get("/nested/projects/2/events")
        with client.websocket_connect("/nested/ws") as socket:
            socket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "projects.cancel",
                    "params": {"project_id": 0},
                }
            )
            rpc = socket.receive_json()
    assert response.json()["type"] == "urn:first:problem/project_missing"
    assert "urn:first:problem/project_missing" in terminal.text
    assert rpc["error"]["data"]["problem"]["type"] == response.json()["type"]
    with TestClient(second) as client:
        assert client.get("/nested/projects/0").json()["type"] == (
            "urn:second:problem/project_missing"
        )
    assert feature.errors == (project_missing,)
    contracts = export_contracts(first)
    assert contracts.openrpc is not None
    assert contracts.openrpc["methods"][0]["errors"][0]["x-canon-problem-type"] == (
        "urn:first:problem/project_missing"
    )


async def test_rpc_router_contributes_catalog_and_uses_composition_type_base() -> None:
    from fastapi import FastAPI
    from websockets.asyncio.client import connect

    from tests.unit.integrations.webrtc.server import serve

    channel = RpcChannel("projects")

    @channel.method(raises=[project_missing])
    async def missing() -> None:
        from examples.canonical_errors.app import ProjectMissing

        raise ProjectMissing("secret")

    system = RpcChannel("system")

    async def session(tenant: str) -> str:
        return tenant

    @system.method()
    async def whoami(tenant: Inject[str]) -> str:
        return tenant

    router = CanonWebSocketRouter(
        "/ws/{tenant}", channels=[channel, system], context=session
    )
    app = Composition(
        features=(Feature(name="projects", routers=[router]),),
        errors=ErrorOptions(type_base="urn:application:problems"),
    ).build(FastAPI())
    async with (
        asyncio.timeout(15),
        serve(app) as base_url,
        connect(f"{base_url}/ws/demo") as socket,
    ):
        await socket.send(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "system.whoami"})
        )
        assert json.loads(await socket.recv()) == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": "demo",
        }
        await socket.send(
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "projects.missing"})
        )
        problem = json.loads(await socket.recv())["error"]["data"]["problem"]
    assert problem["type"] == "urn:application:problems/project_missing"
    document = router.contract(
        title="Composed", base_url="wss://example.test", app=app
    ).to_openrpc()
    methods = {method["name"]: method for method in document["methods"]}
    assert set(methods) == {"projects.missing", "system.whoami"}
    assert (
        methods["projects.missing"]["errors"][0]["x-canon-problem-type"]
        == problem["type"]
    )
    assert document["servers"][0]["url"] == "wss://example.test/ws/{tenant}"


async def test_rpc_router_serves_all_endpoint_kinds_with_one_contract() -> None:
    from fastapi import FastAPI, WebSocket
    from websockets.asyncio.client import connect

    from fastapi_canon.integrations.websockets import RpcSocket
    from tests.unit.integrations.webrtc.server import serve

    channel = RpcChannel("demo")

    @channel.method()
    async def ping() -> str:
        return "pong"

    @channel.stream()  # type: ignore[arg-type]  # Runtime checks require a free function.
    async def frames() -> AsyncIterator[bytes]:
        yield b"frame"

    @channel.socket()  # type: ignore[arg-type]  # Runtime checks require a free function.
    async def echo(socket: Inject[RpcSocket]) -> None:
        await socket.send(await socket.receive())

    async def custom(socket: WebSocket) -> None:
        await socket.accept()
        await socket.send_text("custom")
        await socket.close()

    router = CanonWebSocketRouter(version=2, prefix="/api")
    router.socket("/rpc", channels=[channel])
    router.stream("/frames", frames)
    router.socket("/echo", echo)
    router.socket("/custom", channels=[RpcChannel("custom")], serve=custom)
    app = Composition(features=[Feature(name="demo", routers=[router])]).build(
        FastAPI()
    )
    async with asyncio.timeout(15), serve(app) as base_url:
        async with connect(f"{base_url}/api/rpc") as socket:
            await socket.send('{"jsonrpc":"2.0","id":1,"method":"demo.ping"}')
            assert json.loads(await socket.recv())["result"] == "pong"
        async with connect(f"{base_url}/api/frames") as socket:
            assert await socket.recv() == b"frame"
        async with connect(f"{base_url}/api/echo") as socket:
            await socket.send("echo")
            assert await socket.recv() == "echo"
        async with connect(f"{base_url}/api/custom") as socket:
            assert await socket.recv() == "custom"
    document = router.contract(title="Demo", base_url="wss://example.test").to_openrpc()
    assert document["info"]["version"] == "2.0.0"
    assert {server["url"] for server in document["servers"]} == {
        "wss://example.test/api/rpc",
        "wss://example.test/api/custom",
    }


def test_nested_public_extensions_keep_aliases_and_valid_contract_references() -> None:
    from fastapi import FastAPI
    from pydantic import BaseModel, Field

    from fastapi_canon import APIRouter, Error

    class Retry(BaseModel):
        retry_after: int = Field(alias="retryAfter")

    class PublicFields(BaseModel):
        recovery: Retry

    class Missing(Exception):
        pass

    def public_fields(_: Missing) -> PublicFields:
        return PublicFields(recovery=Retry(retryAfter=5))

    error = Error(
        Missing,
        status=404,
        code="rich_missing",
        title="Missing",
        extensions_model=PublicFields,
        extensions=public_fields,
    )
    channel = RpcChannel("items")

    @channel.method(raises=[error])
    async def missing() -> None:
        raise Missing("private")

    router = APIRouter()
    router.include_channel(channel, path="/ws")
    app = Composition(
        features=[Feature(name="items", routers=[router])],
        errors=ErrorOptions(type_base="urn:test:problems"),
    ).build(FastAPI())
    contracts = export_contracts(app)
    assert contracts.openrpc is not None
    document = contracts.openrpc
    with TestClient(app) as client, client.websocket_connect("/ws") as socket:
        socket.send_json({"jsonrpc": "2.0", "id": 1, "method": "items.missing"})
        payload = socket.receive_json()["error"]["data"]["problem"]
    assert payload["recovery"] == {"retryAfter": 5}
    assert "private" not in json.dumps(payload)
    schema = {
        "$ref": "#/components/schemas/RichMissingProblem",
        "components": document["components"],
    }
    Draft202012Validator(schema).validate(payload)
    with pytest.raises(SchemaValidationError) as caught:
        Draft202012Validator(schema).validate(
            {**payload, "recovery": {"retryAfter": "bad"}}
        )
    assert "not of type" in str(caught.value)


@pytest.mark.asyncio
async def test_generated_python_stream_consumes_controls_and_exposes_typed_problem(
    tmp_path: Path,
) -> None:
    app = create_app()
    files = generate_python(stream_ir(export_schema(app)))
    with _sdk(tmp_path, "canonical_sse_sdk", files) as sdk:
        async with asyncio.timeout(15):
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as http,
                sdk.Client("http://test", http_client=http) as client,
            ):
                values = [
                    value
                    async for value in client.projects.projects_events(project_id=1)
                ]
                assert [value.sequence for value in values] == [1]
                with pytest.raises(sdk.ProblemError) as caught:
                    _ = [
                        value
                        async for value in client.projects.projects_events(project_id=2)
                    ]
                assert caught.value.problem.replacement is None
                assert caught.value.problem.code == "project_missing"
                assert (
                    type(caught.value.problem).__name__
                    == "ProjectMissingProblemEmbedded"
                )
                with pytest.raises(sdk.ProblemError) as http_error:
                    _ = [
                        value
                        async for value in client.projects.projects_events(project_id=0)
                    ]
                assert http_error.value.problem.status == 404


@pytest.mark.asyncio
async def test_python_stream_rejects_incomplete_or_malformed_controls_and_cleans_up(
    tmp_path: Path,
) -> None:
    files = generate_python(stream_ir(export_schema(create_app())))
    with _sdk(tmp_path, "canonical_sse_sdk", files) as sdk:
        async with asyncio.timeout(15):
            for wire, error_type in [
                ('data: {"sequence":1}\n\n', sdk.TransportError),
                ("event: canon.complete\ndata: null\n\n", sdk.ProtocolError),
                ('event: canon.problem\ndata: {"title":"bad"}\n\n', sdk.ProtocolError),
                ("event: canon.future\ndata: {}\n\n", sdk.ProtocolError),
                ("event: canon.complete\ndata: {}", sdk.TransportError),
            ]:
                response = httpx.Response(
                    200, text=wire, headers={"content-type": "text/event-stream"}
                )
                async with (
                    httpx.AsyncClient(
                        transport=httpx.MockTransport(lambda _, reply=response: reply)
                    ) as http,
                    sdk.Client("https://test", http_client=http) as client,
                ):
                    with pytest.raises(error_type):
                        _ = [
                            value
                            async for value in client.projects.projects_events(
                                project_id=1
                            )
                        ]
                assert response.is_closed


def test_stream_shared_declarations_and_safe_renderer_failures() -> None:
    from fastapi import FastAPI

    from examples.canonical_errors.app import ProjectEvent, ProjectMissing
    from fastapi_canon import Error
    from fastapi_canon.integrations.sse.canon import CanonStreamRouter

    def bad_detail(_: ProjectMissing) -> str:
        raise ValueError("private renderer failure")

    error = Error(
        ProjectMissing,
        status=404,
        code="project_missing",
        title="Missing",
        detail=bad_detail,
    )
    router = CanonStreamRouter(raises=[error])
    closed = []

    @router.stream("/events", raises=[error])
    def events() -> Iterator[ProjectEvent]:
        try:
            yield ProjectEvent(sequence=1)
            raise ProjectMissing("private exception")
        finally:
            closed.append(True)

    app = Composition(features=(Feature(name="events", routers=[router]),)).build(
        FastAPI()
    )
    response = TestClient(app).get("/events")
    assert closed == [True]
    assert "canon.problem" in response.text and "canon.complete" not in response.text
    assert "internal_server_error" in response.text and "private" not in response.text
    assert (
        len(app.openapi()["paths"]["/events"]["get"]["x-canon-stream"]["problems"]) == 1
    )


@pytest.mark.asyncio
async def test_cancelled_server_stream_runs_cleanup_without_terminal_problem() -> None:
    from fastapi import FastAPI

    from examples.canonical_errors.app import ProjectEvent
    from fastapi_canon.integrations.sse.canon import CanonStreamRouter

    router = CanonStreamRouter()
    closed = []

    @router.stream("/events")
    async def events() -> AsyncIterator[ProjectEvent]:
        try:
            yield ProjectEvent(sequence=1)
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    app = Composition(features=(Feature(name="events", routers=[router]),)).build(
        FastAPI()
    )

    async with asyncio.timeout(15):
        started = asyncio.Event()
        messages = []
        first = True

        async def receive() -> Any:
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b"", "more_body": False}
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(message: Any) -> None:
            messages.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                started.set()

        scope: Any = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "method": "GET",
            "scheme": "http",
            "path": "/events",
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 1),
            "http_version": "1.1",
        }
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(app(scope, receive, send), timeout=5)
        assert closed == [True]
        assert b"canon.problem" not in b"".join(
            message.get("body", b"") for message in messages
        )
        assert b"canon.complete" not in b"".join(
            message.get("body", b"") for message in messages
        )


def test_generated_python_rpc_error_uses_type_uri_and_preserves_extensions(
    tmp_path: Path,
) -> None:
    files = dict(
        render_python_client(
            _example_openrpc(),
            PythonClientOptions(
                package="canonical_rpc_sdk", with_transport="websocket"
            ),
        )
    )
    payload = {
        "type": "https://api.example.com/problems/project_missing",
        "title": "Project not found",
        "code": "project_missing",
        "replacement": None,
        "future": {"new": True},
    }
    with _sdk(tmp_path, "canonical_rpc_sdk", files) as sdk:
        errors = importlib.import_module("canonical_rpc_sdk.errors")
        error = errors.error_from_response(
            -32000, "", "Project not found", None, payload
        )
        assert isinstance(error, sdk.ProblemError)
        assert isinstance(error, sdk.ProjectMissingError)
        assert error.problem.replacement is None
        assert error.problem.model_dump()["future"] == {"new": True}
        unknown = errors.error_from_response(
            -32000,
            "",
            "Future problem",
            None,
            {**payload, "type": "urn:future:problem"},
        )
        assert isinstance(unknown, sdk.ProblemError)
        assert not isinstance(unknown, sdk.ProjectMissingError)
        with pytest.raises(sdk.ProtocolError):
            errors.error_from_response(
                -32000, "", "Invalid", None, {**payload, "status": 404}
            )

        async def request() -> None:
            from tests.unit.integrations.webrtc.server import serve

            transport_module = importlib.import_module("canonical_rpc_sdk.transport")
            async with asyncio.timeout(15), serve(create_app()) as base_url:
                transport = await transport_module.WebSocketTransport.open(
                    f"{base_url}/ws"
                )
                client = sdk.ProjectsClient(transport)
                try:
                    with pytest.raises(sdk.ProjectMissingError) as caught:
                        await client.projects.cancel(project_id=0)
                    assert isinstance(caught.value, sdk.ProblemError)
                    assert caught.value.problem.replacement is None
                    await client.projects.cancel(project_id=1)
                finally:
                    await client.close()

        asyncio.run(request())


def test_generated_typescript_clients_execute_problem_and_stream_lifecycle(
    tmp_path: Path,
) -> None:
    import shutil
    import subprocess

    from fastapi_canon.integrations.sse.codegen.typescript import generate_typescript
    from fastapi_canon.integrations.websockets.codegen import (
        TypeScriptClientOptions,
        render_typescript_client,
    )

    node = shutil.which("node")
    compiler = Path(__file__).parents[3] / "node_modules/typescript/bin/tsc"
    if node is None or not compiler.exists():
        pytest.skip("TypeScript execution requires the workspace Node dependencies")
    sdk_files = {
        "sse": generate_typescript(
            stream_ir(export_schema(create_app()), target="typescript")
        ),
        "rpc": dict(
            render_typescript_client(
                _example_openrpc(),
                TypeScriptClientOptions(
                    client_name="ProjectsClient", with_transport="websocket"
                ),
            )
        ),
    }
    paths = []
    for sdk, files in sdk_files.items():
        for name, text in files.items():
            if not name.endswith(".ts"):
                continue
            target = tmp_path / sdk / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            paths.append(str(target))
    result = subprocess.run(
        [
            node,
            str(compiler),
            "--target",
            "es2022",
            "--module",
            "commonjs",
            "--lib",
            "es2022,dom,esnext.disposable",
            "--strict",
            "--skipLibCheck",
            "--outDir",
            str(tmp_path / "js"),
            *paths,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    runner = tmp_path / "execute.cjs"
    runner.write_text(
        r"""const assert = require("node:assert/strict");
const sse = require("./js/sse/index.js");
const rpc = require("./js/rpc/index.js");
const rpcErrors = require("./js/rpc/errors.js");
const payload = {type: "https://api.example.com/problems/project_missing", title: "Project not found", code: "project_missing", replacement: null, future: true};
const collect = async (stream) => { const values = []; for await (const v of stream) values.push(v); return values; };
const client = (wire, status = 200, type = "text/event-stream") => sse.createClient({baseUrl: "https://test", fetch: async () => new Response(wire, {status, headers: {"content-type": type}})});
(async () => {
  assert.deepEqual(await collect(client('data: {"sequence":1}\n\nevent: canon.complete\ndata: {}\n\n').projects.projectsEvents({project_id: 1})), [{sequence:1}]);
  await assert.rejects(collect(client(`event: canon.problem\ndata: ${JSON.stringify(payload)}\n\n`).projects.projectsEvents({project_id:2})), e => e instanceof sse.ProblemError && e.problem.replacement === null && e.problem.future === true);
  await assert.rejects(collect(client('data: {"sequence":1}\n\n').projects.projectsEvents({project_id:1})), sse.TransportError);
  await assert.rejects(collect(client('event: canon.complete\ndata: null\n\n').projects.projectsEvents({project_id:1})), sse.ProtocolError);
  await assert.rejects(collect(client(JSON.stringify({...payload,status:404}),404,"application/problem+json").projects.projectsEvents({project_id:0})), e => e instanceof sse.ProblemError && e.problem.status === 404);
  const error = rpcErrors.errorFromResponse(-32000, "", "Missing", undefined, payload);
  const listeners = new Map();
  const socket = {
    readyState: 1,
    addEventListener: (name, listener) => listeners.set(name, listener),
    removeEventListener: (name) => listeners.delete(name),
    send: text => {
      const request = JSON.parse(text);
      queueMicrotask(() => listeners.get("message")?.({data:JSON.stringify({jsonrpc:"2.0",id:request.id,error:{code:-32000,message:"Missing",data:{problem:payload}}})}));
    },
    close: () => { socket.readyState = 3; listeners.get("close")?.({code:1000,reason:"done"}); },
  };
  const transport = await rpc.WebSocketTransport.open("wss://test", {socketFactory: () => socket});
  const rpcClient = new rpc.ProjectsClient(transport);
  await assert.rejects(rpcClient.projects.cancel({projectId:0}), e => e instanceof rpc.ProblemError && e instanceof rpc.ProjectMissingError);
  await rpcClient.close();
  assert(error instanceof rpc.ProblemError);
  assert(error instanceof rpc.ProjectMissingError);
  assert.equal(error.problem.replacement, null);
  assert.throws(() => rpcErrors.errorFromResponse(-32000,"","Bad",undefined,{...payload,status:404}),rpc.ProtocolError);
})().catch(e => { console.error(e); process.exitCode = 1; });
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(runner)], capture_output=True, text=True, check=False, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


@contextmanager
def _sdk(root: Path, name: str, files: dict[str, str]) -> Iterator[Any]:
    destination = root / name
    for path, content in files.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    sys.path.insert(0, str(root))
    try:
        yield importlib.import_module(name)
    finally:
        sys.path.remove(str(root))
        for module in tuple(sys.modules):
            if module == name or module.startswith(name + "."):
                del sys.modules[module]


def _example_openrpc() -> dict[str, Any]:
    document = export_contracts(create_app(), kinds=("openrpc",)).openrpc
    assert document is not None
    return document
