from __future__ import annotations

import asyncio
import importlib
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from fastapi_canon import APIRouter, Composition, Error, Feature, export_contracts
from fastapi_canon.codegen.config import SourceConfig, TargetConfig
from fastapi_canon.codegen.planner import build_plan
from fastapi_canon.codegen.render import render
from fastapi_canon.integrations.websockets import RpcChannel
from tests.unit.integrations.webrtc.server import serve


class Project(BaseModel):
    id: int


class CancelProject(BaseModel):
    project_id: int
    delay_ms: int = 0


class ProjectMissing(Exception):
    pass


class SharedProject(BaseModel):
    model_config = ConfigDict(
        extra="allow", json_schema_extra={"x-canon-model-id": "urn:test:shared-project"}
    )
    project_name: str
    counts: dict[str, int]


@pytest.fixture
def sdk_app() -> FastAPI:
    missing = Error(
        ProjectMissing, status=404, code="project_missing", title="Project unavailable"
    )
    router = APIRouter(namespace="projects")
    channel = RpcChannel("projects")

    @router.get(
        "/projects/{project_id}",
        raises=[missing],
        summary="Load one project.",
        response_model=Project,
    )
    async def get(project_id: int, delay_ms: int = 0) -> Project | JSONResponse:
        """Return the public project representation."""
        await asyncio.sleep(delay_ms / 1000)
        if project_id == 0:
            raise ProjectMissing("Private database details")
        if project_id == 2:
            return JSONResponse(
                {
                    "type": "urn:other:missing",
                    "code": "project_missing",
                    "title": "Unavailable",
                },
                status_code=404,
                media_type="application/problem+json",
            )
        if project_id == 3:
            return JSONResponse(
                {"internal": "Proxy diagnostics"},
                status_code=429,
                headers={"Retry-After": "2"},
            )
        if project_id == 4:
            return JSONResponse(
                {"type": "urn:broken"},
                status_code=500,
                media_type="application/problem+json",
            )
        return Project(id=project_id)

    async def admission(open_delay_ms: int = 0) -> None:
        await asyncio.sleep(open_delay_ms / 1000)

    @router.sse(
        "/events/{project_id}", raises=[missing], dependencies=[Depends(admission)]
    )
    async def events(project_id: int, delay_ms: int = 0) -> AsyncIterator[Project]:
        await asyncio.sleep(delay_ms / 1000)
        if project_id == 0:
            raise ProjectMissing("Private stream details")
        yield Project(id=project_id)
        await asyncio.sleep(delay_ms / 1000)
        yield Project(id=project_id + 1)

    @channel.method(raises=[missing])
    async def cancel(params: CancelProject) -> None:
        await asyncio.sleep(params.delay_ms / 1000)
        if params.project_id == 0:
            raise ProjectMissing("Private RPC details")

    @channel.method()
    async def rpc() -> int:
        return 13

    router.include_channel(channel, path="/ws")
    return Composition(features=[Feature(name="projects", routers=[router])]).build(
        FastAPI()
    )


async def test_python_sdk_error_identity_client_timeouts_and_relocation(
    tmp_path: Path, sdk_app: FastAPI
) -> None:
    _generate_sdk(tmp_path / "consumer" / "sdk", sdk_app, "python")
    (tmp_path / "consumer" / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        sdk: Any = importlib.import_module("consumer.sdk")
        async with asyncio.timeout(20), serve(sdk_app) as address:
            base_url = address.replace("ws://", "http://")
            async with (
                httpx.AsyncClient(timeout=0.02) as http,
                sdk.ApiClient(base_url, http_client=http) as client,
            ):
                assert (
                    "Return the public project representation."
                    in client.projects.get.__doc__
                )
                assert "ProjectMissingError" in client.projects.get.__doc__
                with pytest.raises(sdk.TransportError):
                    await client.projects.get(project_id=1, delay_ms=200)
                assert (
                    await client.projects.get(
                        project_id=1,
                        delay_ms=40,
                        options=sdk.RequestOptions(timeout=None),
                    )
                ).id == 1
                with pytest.raises(sdk.ProjectMissingError) as http_error:
                    await client.projects.get(project_id=0)
                with pytest.raises(sdk.ProjectMissingError) as stream_error:
                    async for _ in client.projects.events(project_id=0):
                        pass
                with pytest.raises(sdk.ProjectMissingError) as rpc_error:
                    await client.projects.cancel(project_id=0)
                assert (
                    http_error.value.problem.code
                    == stream_error.value.problem.code
                    == rpc_error.value.problem.code
                    == "project_missing"
                )
                assert "Private" not in str(http_error.value)
                with pytest.raises(sdk.HttpError) as proxy:
                    await client.projects.get(project_id=3)
                assert proxy.value.response.headers["Retry-After"] == "2"
                assert proxy.value.details == {"internal": "Proxy diagnostics"}
                assert "Proxy diagnostics" not in str(proxy.value)
    finally:
        sys.path.remove(str(tmp_path))
        for name in tuple(sys.modules):
            if name == "consumer" or name.startswith("consumer."):
                del sys.modules[name]


async def test_typescript_sdk_esm_safe_errors_deadlines_and_disposal(
    tmp_path: Path, sdk_app: FastAPI
) -> None:
    node = shutil.which("node")
    compiler = Path(__file__).resolve().parents[3] / "node_modules/typescript/bin/tsc"
    if node is None or not compiler.exists():
        pytest.skip("Run pnpm install --frozen-lockfile --ignore-scripts")
    _generate_sdk(tmp_path / "sdk", sdk_app, "typescript")
    (tmp_path / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    (tmp_path / "usage.ts").write_text(
        """
import { createClient, ProjectMissingError, ProblemError, HttpError, ProtocolError, type Project } from "./sdk/index.js";
// @ts-expect-error Protocol envelopes are not part of the public SDK surface.
import type { ProjectsCancelRequest } from "./sdk/index.js";

function check(value: unknown): asserts value { if (!value) throw new Error("Consumer assertion failed"); }
async function rejects(invoke: () => Promise<unknown>, matches: (error: unknown) => boolean) {
  try { await invoke(); } catch (error) { check(matches(error)); return; }
  throw new Error("Expected rejection");
}

export async function run(baseUrl: string) {
  const client = createClient({baseUrl});
  {
    await using owned = client;
    const value: Project = await owned.projects.get({projectId:1});
    check(value.id === 1);
    check(await owned.projects.rpc({timeoutMs:1_000}) === 13);
    const success = await owned.projects.get.safe({projectId:1});
    check(success.ok && success.data.id === 1);
    const missing = await owned.projects.get.safe({projectId:0});
    check(!missing.ok && missing.kind === "declared" && missing.problem.code === "project_missing");
    const unknown = await owned.projects.get.safe({projectId:2});
    check(!unknown.ok && unknown.kind === "unexpected");
    await rejects(() => owned.projects.get({projectId:0}), error => error instanceof ProjectMissingError && error.problem.code === "project_missing");
    await rejects(() => owned.projects.cancel({projectId:0}), error => error instanceof ProjectMissingError);
    await rejects(async () => { for await (const _ of owned.projects.events({projectId:0})) {} }, error => error instanceof ProjectMissingError);
    await rejects(() => owned.projects.get.safe({projectId:3}), error => error instanceof HttpError && error.response.headers.get("Retry-After") === "2" && !JSON.stringify(error).includes("Proxy diagnostics") && !String(error).includes("Proxy diagnostics"));
    await rejects(() => owned.projects.get.safe({projectId:4}), error => error instanceof ProtocolError);
    await rejects(() => owned.projects.get({projectId:1,delayMs:200}, {timeoutMs:10}), error => error instanceof DOMException && error.name === "TimeoutError");
    await rejects(() => owned.projects.cancel.safe({projectId:1,delayMs:200}, {timeoutMs:10}), error => error instanceof DOMException && error.name === "TimeoutError");
    await rejects(async () => { for await (const _ of owned.projects.events({projectId:1,openDelayMs:200}, {openTimeoutMs:10})) {} }, error => error instanceof DOMException && error.name === "TimeoutError");
    await rejects(async () => { for await (const _ of owned.projects.events({projectId:1,delayMs:200}, {idleTimeoutMs:10})) {} }, error => error instanceof DOMException && error.name === "TimeoutError");
    const signal = AbortSignal.abort(new DOMException("Cancelled", "AbortError"));
    await rejects(() => owned.projects.get.safe({projectId:1}, {signal}), error => error instanceof DOMException && error.name === "AbortError");
    let events = 0;
    for await (const event of owned.projects.events({projectId:1}, {idleTimeoutMs:100})) { check(event.id > 0); events++; await new Promise(resolve => setTimeout(resolve,120)); }
    check(events === 2);
  }
  await rejects(() => client.projects.get({projectId:1}), error => error instanceof Error && !(error instanceof ProblemError));
}

function compileOnly() {
  const client = createClient({baseUrl:"https://test"});
  // @ts-expect-error RPC options do not accept per-request HTTP headers.
  client.projects.cancel({projectId:1}, {headers:{Authorization:"secret"}});
  // @ts-expect-error Stream timeouts distinguish opening from business-event idle time.
  client.projects.events({projectId:1}, {timeoutMs:1});
}
void compileOnly;
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            node,
            str(compiler),
            "--strict",
            "--verbatimModuleSyntax",
            "--exactOptionalPropertyTypes",
            "--noUnusedLocals",
            "--noUnusedParameters",
            "--target",
            "es2022",
            "--module",
            "nodenext",
            "--moduleResolution",
            "nodenext",
            "--lib",
            "es2022,dom,esnext.disposable",
            "--outDir",
            str(tmp_path / "js"),
            str(tmp_path / "usage.ts"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (tmp_path / "runner.mjs").write_text(
        'import {run} from "./js/usage.js"; await run(process.argv[2]);',
        encoding="utf-8",
    )
    async with asyncio.timeout(30), serve(sdk_app) as address:
        process = await asyncio.create_subprocess_exec(
            node,
            str(tmp_path / "runner.mjs"),
            address.replace("ws://", "http://"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, stdout.decode() + stderr.decode()


@pytest.mark.parametrize("stop", ["close", "timeout", "abort", "connect_abort"])
async def test_typescript_sdk_close_cancels_owned_pending_handshake(
    tmp_path: Path, sdk_app: FastAPI, stop: str
) -> None:
    node = shutil.which("node")
    compiler = Path(__file__).resolve().parents[3] / "node_modules/typescript/bin/tsc"
    if node is None or not compiler.is_file():
        pytest.skip("Run pnpm install --frozen-lockfile --ignore-scripts")
    _generate_sdk(tmp_path / "sdk", sdk_app, "typescript")
    (tmp_path / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    compiled = subprocess.run(
        [
            node,
            str(compiler),
            "--strict",
            "--verbatimModuleSyntax",
            "--exactOptionalPropertyTypes",
            "--noUnusedLocals",
            "--noUnusedParameters",
            "--target",
            "es2022",
            "--module",
            "nodenext",
            "--moduleResolution",
            "nodenext",
            "--lib",
            "es2022,dom,esnext.disposable",
            "--outDir",
            str(tmp_path / "js"),
            str(tmp_path / "sdk/index.ts"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    runner = tmp_path / "runner.mjs"
    runner.write_text(
        """import assert from "node:assert/strict";
import net from "node:net";
import {once} from "node:events";
import {createClient, TransportError} from "./js/index.js";

async function deadline(operation) {
  let timer;
  try {
    return await Promise.race([operation, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("Lifecycle deadline exceeded")), 1_000);
    })]);
  } finally { clearTimeout(timer); }
}
const sockets = new Set();
let resolveOpened, resolveDisconnected;
const opened = new Promise(resolve => {resolveOpened = resolve;});
const disconnected = new Promise(resolve => {resolveDisconnected = resolve;});
const server = net.createServer(socket => {
  sockets.add(socket);
  socket.on("data", () => resolveOpened());
  socket.on("end", () => socket.end());
  socket.on("close", () => {sockets.delete(socket); resolveDisconnected();});
});
server.listen(0, "127.0.0.1");
await once(server, "listening");
const connectionController = new AbortController();
const client = createClient({
  baseUrl: `http://127.0.0.1:${server.address().port}`,
  bindings: {control: {connect: {
    lazy: false, requestTimeoutMs: null, signal: connectionController.signal,
  }}},
});
try {
  const stop = process.argv[2];
  const controller = new AbortController();
  const reason = new DOMException("Cancelled by caller", "AbortError");
  const options = stop === "timeout" ? {timeoutMs: 100} : {signal: controller.signal};
  const first = client.projects.rpc(options).catch(error => error);
  const sibling = client.projects.rpc().catch(error => error);
  await deadline(opened);
  if (stop === "abort") controller.abort(reason);
  if (stop === "connect_abort") connectionController.abort(reason);
  if (stop !== "close") {
    const error = await deadline(first);
    assert.equal(error.name, stop === "timeout" ? "TimeoutError" : "AbortError");
    if (stop !== "timeout") assert.equal(error, reason);
    if (stop !== "connect_abort") {
      assert.equal(sockets.size, 1, "One call must not close shared initialization");
    }
  }
  await deadline(client.close());
  assert.ok(await deadline(first) instanceof Error);
  assert.ok(await deadline(sibling) instanceof Error);
  await deadline(disconnected);
  assert.equal(sockets.size, 0, "SDK close must release the pending handshake");
  await assert.rejects(() => client.projects.rpc(), TransportError);
} finally {
  for (const socket of sockets) socket.destroy();
  await deadline(client.close());
  await deadline(new Promise(resolve => server.close(resolve)));
}
""",
        encoding="utf-8",
    )
    async with asyncio.timeout(20):
        process = await asyncio.create_subprocess_exec(
            node,
            str(runner),
            stop,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    assert process.returncode == 0, stdout.decode() + stderr.decode()


@pytest.mark.parametrize("tags", [[], ["admin"], ["Admin Panel"]])
async def test_sdk_operation_names_are_independent_of_documentation_tags(
    tmp_path: Path, tags: list[str | Enum]
) -> None:
    router = APIRouter()

    @router.get("/stats", tags=tags)
    async def stats() -> int:
        return 13

    app = Composition(features=[Feature(name="stats", routers=[router])]).build(
        FastAPI()
    )
    contracts = export_contracts(app)
    assert contracts.openapi is not None
    plan = build_plan(((SourceConfig("api", "openapi"), contracts.openapi),))
    for name, content in render(
        plan, TargetConfig("python", "python", ("api",), tmp_path, package="tagged_sdk")
    ).items():
        path = tmp_path / "tagged_sdk" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        sdk: Any = importlib.import_module("tagged_sdk")
        async with (
            asyncio.timeout(5),
            httpx.AsyncClient(transport=httpx.ASGITransport(app)) as http,
            sdk.ApiClient("https://test", http_client=http) as client,
        ):
            assert await client.stats() == 13
    finally:
        sys.path.remove(str(tmp_path))
        for name in tuple(sys.modules):
            if name == "tagged_sdk" or name.startswith("tagged_sdk."):
                del sys.modules[name]


async def test_python_sdk_close_cancels_owned_pending_handshake(
    tmp_path: Path, sdk_app: FastAPI
) -> None:
    _generate_sdk(tmp_path / "generated_sdk", sdk_app, "python")
    opened, disconnected = asyncio.Event(), asyncio.Event()
    peers: set[asyncio.StreamWriter] = set()

    async def stall(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peers.add(writer)
        try:
            await reader.readuntil(b"\r\n\r\n")
            opened.set()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            peers.discard(writer)
            disconnected.set()

    sys.path.insert(0, str(tmp_path))
    try:
        sdk: Any = importlib.import_module("generated_sdk")
        async with (
            asyncio.timeout(5),
            await asyncio.start_server(stall, "127.0.0.1", 0) as server,
        ):
            port = server.sockets[0].getsockname()[1]
            client = sdk.ApiClient(
                f"http://127.0.0.1:{port}", bindings={"control": {"lazy": False}}
            )
            try:
                request = asyncio.create_task(
                    client.projects.cancel(
                        project_id=1, options=sdk.RequestOptions(timeout=0.1)
                    )
                )
                await opened.wait()
                with pytest.raises(sdk.TransportError, match="timed out"):
                    await request
                await asyncio.wait_for(client.aclose(), timeout=1)
                await disconnected.wait()
            finally:
                for writer in tuple(peers):
                    writer.close()
                await client.aclose()
    finally:
        sys.path.remove(str(tmp_path))
        for name in tuple(sys.modules):
            if name == "generated_sdk" or name.startswith("generated_sdk."):
                del sys.modules[name]


def test_python_public_api_checks_forwarded_keywords_and_binding_options(
    tmp_path: Path, sdk_app: FastAPI
) -> None:
    _generate_sdk(tmp_path / "generated_sdk", sdk_app, "python")
    usage = tmp_path / "usage.py"
    usage.write_text(
        """from httpx import AsyncClient
from fastapi_canon import APIRouter
from generated_sdk import ApiClient, ControlConnectionOptions

router = APIRouter(namespace="projects", prefix="/v1", redirect_slashes=False)
router.get("/projects", response_model=list[str], status_code=201)
router.api_route("/projects", methods=["GET"], deprecated=True)
router.include_router(APIRouter(), prefix="/nested")
options: ControlConnectionOptions = {"lazy": False, "reconnect": True, "retry_attempts": 2}
ApiClient("https://test", http_client=AsyncClient(), bindings={"control": options})

APIRouter(prefx="/v1")  # type: ignore[call-arg]
router.get("/projects", status_code="201")  # type: ignore[arg-type]
router.get("/projects", summery="Projects")  # type: ignore[call-arg]
router.include_router(APIRouter(), prefex="/nested")  # type: ignore[call-arg]
ApiClient("https://test", http_client=object())  # type: ignore[arg-type]
ApiClient("https://test", bindings={"control": {"reconect": True}})  # type: ignore[arg-type]
ApiClient("https://test", clients={"typo": None})  # type: ignore[arg-type]
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--follow-imports=silent",
            "--warn-unused-ignores",
            "--no-incremental",
            str(usage),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def test_shared_dto_and_changed_problem_titles_work_in_both_sdks(
    tmp_path: Path,
) -> None:
    baseline = _shared_application("Original public title")
    _generate_sdk(tmp_path / "generated_sdk", baseline, "python")
    _generate_sdk(tmp_path / "ts", baseline, "typescript")
    sys.path.insert(0, str(tmp_path))
    try:
        sdk: Any = importlib.import_module("generated_sdk")
        models = importlib.import_module("generated_sdk.models")
        rpc_models = importlib.import_module(
            "generated_sdk.internal.control_jsonrpc.models"
        )
        assert models.SharedProject is rpc_models.SharedProject
        updated = _shared_application("Revised public title")
        async with asyncio.timeout(30), serve(updated) as address:
            base_url = address.replace("ws://", "http://")
            async with sdk.ApiClient(base_url) as client:
                project = await client.projects.get(project_name="canon")
                echoed = await client.projects.echo(
                    project_name="canon", counts={"items": 1}
                )
                assert type(project) is type(echoed) is models.SharedProject
                assert project.project_name == echoed.project_name == "canon"
                assert project.model_dump()["future"] is True
                streamed = [
                    item async for item in client.projects.events(project_name="canon")
                ]
                assert type(streamed[0]) is models.SharedProject
                with pytest.raises(sdk.ProjectMissingError) as http_error:
                    await client.projects.get(project_name="missing")
                with pytest.raises(sdk.ProjectMissingError) as rpc_error:
                    await client.projects.echo(project_name="missing", counts={})
                with pytest.raises(sdk.ProjectMissingError) as stream_error:
                    async for _ in client.projects.events(project_name="missing"):
                        pass
                assert (
                    http_error.value.problem.title
                    == rpc_error.value.problem.title
                    == stream_error.value.problem.title
                    == "Revised public title"
                )
                assert "Private" not in str(http_error.value)
            node = shutil.which("node")
            compiler = (
                Path(__file__).resolve().parents[3] / "node_modules/typescript/bin/tsc"
            )
            if node is None or not compiler.is_file():
                pytest.skip("Run pnpm install --frozen-lockfile --ignore-scripts")
            (tmp_path / "package.json").write_text(
                '{"type":"module"}', encoding="utf-8"
            )
            usage = tmp_path / "usage.ts"
            usage.write_text(
                """import {createClient, ProjectMissingError, type SharedProject} from "./ts/index.js";
declare const process: {argv: string[]};
const client = createClient({baseUrl: process.argv[2]});
const check = (value: boolean) => {if (!value) throw new Error("Contract mismatch");};
try {
  const project: SharedProject = await client.projects.get({projectName:"canon"});
  check(project.project_name === "canon" && project.future === true);
  const echoed: SharedProject = await client.projects.echo({projectName:"canon", counts:{items:1}});
  check(echoed.project_name === "canon");
  for await (const item of client.projects.events({projectName:"canon"})) check(item.project_name === "canon");
  const result = await client.projects.get.safe({projectName:"missing"});
  check(!result.ok && result.kind === "declared" && result.problem.title === "Revised public title");
  try {
    await client.projects.echo({projectName:"missing",counts:{}});
    throw new Error("Expected a problem");
  } catch(error) {
    check(error instanceof ProjectMissingError && error.problem.title === "Revised public title");
  }
} finally {await client.close();}
""",
                encoding="utf-8",
            )
            compiled = subprocess.run(
                [
                    node,
                    str(compiler),
                    "--strict",
                    "--verbatimModuleSyntax",
                    "--exactOptionalPropertyTypes",
                    "--noUnusedLocals",
                    "--noUnusedParameters",
                    "--module",
                    "nodenext",
                    "--target",
                    "es2022",
                    "--lib",
                    "es2022,dom,esnext.disposable",
                    "--outDir",
                    str(tmp_path / "js"),
                    str(usage),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            assert compiled.returncode == 0, compiled.stdout + compiled.stderr
            process = await asyncio.create_subprocess_exec(
                node,
                str(tmp_path / "js/usage.js"),
                base_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            assert process.returncode == 0, stdout.decode() + stderr.decode()
    finally:
        sys.path.remove(str(tmp_path))
        for name in tuple(sys.modules):
            if name == "generated_sdk" or name.startswith("generated_sdk."):
                del sys.modules[name]


def _shared_application(title: str) -> FastAPI:
    missing = Error(ProjectMissing, status=404, code="project_missing", title=title)
    router = APIRouter(namespace="projects")
    channel = RpcChannel("projects")

    @router.get("/projects/{project_name}", raises=[missing])
    async def get(project_name: str) -> SharedProject:
        if project_name == "missing":
            raise ProjectMissing("Private database details")
        return SharedProject.model_validate(
            {"project_name": project_name, "counts": {"items": 1}, "future": True}
        )

    @router.sse("/projects/{project_name}/events", raises=[missing])
    async def events(project_name: str) -> AsyncIterator[SharedProject]:
        yield SharedProject(project_name=project_name, counts={"items": 1})
        if project_name == "missing":
            raise ProjectMissing("Private stream details")

    @channel.method(raises=[missing])
    async def echo(params: SharedProject) -> SharedProject:
        if params.project_name == "missing":
            raise ProjectMissing("Private RPC details")
        return params

    router.include_channel(channel, path="/rpc")
    return Composition(features=[Feature(name="projects", routers=[router])]).build(
        FastAPI()
    )


def _generate_sdk(
    output: Path, app: FastAPI, language: Literal["python", "typescript"]
) -> None:
    contracts = export_contracts(app)
    assert contracts.openapi is not None and contracts.openrpc is not None
    plan = build_plan(
        (
            (SourceConfig("api", "openapi"), contracts.openapi),
            (SourceConfig("control", "openrpc"), contracts.openrpc),
        )
    )
    target = TargetConfig(
        "test", language, ("api", "control"), output, package="generated_sdk"
    )
    for name, content in render(plan, target).items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
