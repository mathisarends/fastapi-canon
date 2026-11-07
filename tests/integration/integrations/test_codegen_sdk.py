from __future__ import annotations

import asyncio
import importlib
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            node,
            str(compiler),
            "--strict",
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


@pytest.mark.parametrize("tags", [[], ["admin"], ["Admin Panel"]])
async def test_sdk_operation_names_are_independent_of_documentation_tags(
    tmp_path: Path, tags: list[str]
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
