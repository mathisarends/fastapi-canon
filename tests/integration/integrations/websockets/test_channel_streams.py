import asyncio
import importlib
import json
import shutil
import socket
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI

from fastapi_canon.codegen.config import SourceConfig, TargetConfig
from fastapi_canon.codegen.planner import build_plan
from fastapi_canon.codegen.render import render
from fastapi_canon.integrations.websockets import (
    CanonWebSocketRouter,
    RpcChannel,
    RpcModel,
)
from fastapi_canon.integrations.websockets.codegen import (
    generate_python_client,
    generate_typescript_client,
)
from fastapi_canon.integrations.websockets.codegen.python import PythonClientOptions
from fastapi_canon.integrations.websockets.codegen.typescript import (
    TypeScriptClientOptions,
)


class Params(RpcModel):
    value: int


async def test_context_opens_before_first_value_and_closes_its_generator(
    tmp_path: Path,
) -> None:
    started, stopped = asyncio.Event(), asyncio.Event()
    channel = RpcChannel("jobs")

    @channel.stream()
    async def progress(params: Params) -> AsyncIterator[int]:
        started.set()
        try:
            await asyncio.Event().wait()
            yield params.value
        finally:
            stopped.set()

    service = CanonWebSocketRouter()
    service.socket("/rpc", channels=[channel])
    async with asyncio.timeout(5), _client(tmp_path, service) as client:
        async with client.jobs.progress(value=1):
            await started.wait()
        await stopped.wait()
        assert stopped.is_set()


@pytest.mark.parametrize("unified", [False, True])
async def test_message_and_binary_streams_share_context_iteration(
    tmp_path: Path,
    unified: bool,
) -> None:
    channel = RpcChannel("results")

    @channel.stream()
    async def numbers(params: Params) -> AsyncIterator[int]:
        yield params.value
        yield params.value + 1

    @channel.stream(content_type="application/octet-stream")
    async def frames() -> AsyncIterator[bytes]:
        yield b"first"
        yield b"second"

    service = CanonWebSocketRouter()
    service.socket("/rpc", channels=[channel])
    service.stream("/frames", frames)
    async with (
        asyncio.timeout(5),
        _client(tmp_path, service, unified=unified) as client,
    ):
        async with client.results.numbers(value=3) as numbers_stream:
            assert [item async for item in numbers_stream] == [3, 4]
        async with client.results.frames() as binary_stream:
            assert [item async for item in binary_stream] == [b"first", b"second"]


async def test_closing_stream_interrupts_a_pending_read_and_cleans_up(
    tmp_path: Path,
) -> None:
    stopped = asyncio.Event()
    channel = RpcChannel("jobs")

    @channel.stream()
    async def progress() -> AsyncIterator[int]:
        try:
            yield 1
            await asyncio.Event().wait()
        finally:
            stopped.set()

    service = CanonWebSocketRouter()
    service.socket("/rpc", channels=[channel])
    async with asyncio.timeout(5), _client(tmp_path, service) as client:
        async with client.jobs.progress() as stream:
            assert await anext(stream) == 1
            reading = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            await stream.aclose()
            with pytest.raises(asyncio.CancelledError):
                await reading
        await stopped.wait()
        assert stopped.is_set()


async def test_client_close_cancels_stream_without_closing_borrowed_transport(
    tmp_path: Path,
) -> None:
    started, stopped = asyncio.Event(), asyncio.Event()
    channel = RpcChannel("jobs")

    @channel.method()
    async def ping() -> str:
        return "pong"

    @channel.stream()
    async def progress() -> AsyncIterator[int]:
        started.set()
        try:
            await asyncio.Event().wait()
            yield 1
        finally:
            stopped.set()

    service = CanonWebSocketRouter()
    service.socket("/rpc", channels=[channel])
    async with asyncio.timeout(5), _client(tmp_path, service):
        sdk = importlib.import_module("stream_integration_client")
        document = json.loads((tmp_path / "openrpc.json").read_text("utf-8"))
        transport = await sdk.WebSocketTransport.open(document["servers"][0]["url"])
        borrowed = sdk.StreamsClient.with_transports(transport, close_transport=False)
        try:
            async with borrowed.jobs.progress():
                await started.wait()
                await borrowed.close()
                await stopped.wait()
                assert await transport.request("jobs.ping") == "pong"
                assert stopped.is_set()
        finally:
            await borrowed.close()
            await transport.close()


async def test_stream_overflow_reports_failure_and_keeps_diagnostics(
    tmp_path: Path,
) -> None:
    overflow, stopped = asyncio.Event(), asyncio.Event()
    observations = []
    channel = RpcChannel("jobs")

    @channel.stream()
    async def progress() -> AsyncIterator[int]:
        try:
            for value in range(12):
                yield value
            await asyncio.Event().wait()
        finally:
            stopped.set()

    def record(method: str | None, dropped: int, source: str) -> None:
        observations.append((method, dropped, source))
        overflow.set()

    service = CanonWebSocketRouter()
    service.socket("/rpc", channels=[channel])
    options = {"notification_queue_size": 1, "on_overflow": record}
    async with (
        asyncio.timeout(5),
        _client(tmp_path, service, options=options) as client,
    ):
        sdk = importlib.import_module("stream_integration_client")
        with pytest.raises(sdk.RpcTransportError, match="overflow"):
            async with client.jobs.progress() as stream:
                await overflow.wait()
                async for _value in stream:
                    pass
        await stopped.wait()
        assert observations[0][1] == 1
        assert observations[0][2] in ("transport", "subscriber")
        assert stopped.is_set()


@pytest.mark.parametrize("unified", [False, True])
async def test_typescript_stream_disposal_cleans_up_a_real_server(
    tmp_path: Path,
    unified: bool,
) -> None:
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    node = shutil.which("node")
    if pnpm is None or node is None:
        pytest.skip("Node.js and pnpm are required")
    stopped = asyncio.Event()
    channel = RpcChannel("jobs")

    @channel.stream()
    async def progress(params: Params) -> AsyncIterator[int]:
        try:
            yield params.value
            yield params.value + 1
            await asyncio.Event().wait()
        finally:
            stopped.set()

    service = CanonWebSocketRouter()
    service.socket("/rpc", channels=[channel])
    async with asyncio.timeout(20), _client(tmp_path, service):
        document = json.loads((tmp_path / "openrpc.json").read_text("utf-8"))
        output = tmp_path / "typescript"
        if unified:
            files = render(
                build_plan(((SourceConfig("rpc", "openrpc"), document),)),
                TargetConfig("typescript", "typescript", ("rpc",), output),
            )
            for name, content in files.items():
                path = output / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            base_url = (
                document["servers"][0]["url"]
                .replace("ws://", "http://")
                .removesuffix("/rpc")
            )
            opening = f"createClient({{baseUrl: {json.dumps(base_url)}}})"
            importing = 'import { createClient } from "./typescript/client";'
        else:
            generate_typescript_client(
                document,
                output,
                TypeScriptClientOptions(
                    client_name="StreamsClient", with_transport="websocket"
                ),
            )
            opening = "await StreamsClient.connect()"
            importing = 'import { StreamsClient } from "./typescript";'
        runner = tmp_path / "stream.test.ts"
        runner.write_text(
            importing
            + f"""
async function main(): Promise<void> {{
  const client = {opening};
  try {{
    await using stream = await client.jobs.progress({{ value: 7 }});
    const received: number[] = [];
    for await (const value of stream) {{
      received.push(value);
      if (received.length === 2) break;
    }}
    if (JSON.stringify(received) !== "[7,8]") throw new Error("Wrong values");
    await stream.closed;
  }} finally {{ await client.close(); }}
  console.log("stream disposed");
}}
void main();
""",
            encoding="utf-8",
        )
        compiled = await asyncio.to_thread(
            subprocess.run,
            [
                pnpm,
                "exec",
                "tsc",
                "--strict",
                "--target",
                "ES2022",
                "--module",
                "Node16",
                "--moduleResolution",
                "Node16",
                "--lib",
                "ES2022,DOM,ESNext.Disposable",
                "--skipLibCheck",
                "--outDir",
                str(tmp_path / "compiled"),
                str(runner),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        exercised = await asyncio.to_thread(
            subprocess.run,
            [node, str(tmp_path / "compiled/stream.test.js")],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert exercised.returncode == 0, exercised.stdout + exercised.stderr
        assert "stream disposed" in exercised.stdout
        await stopped.wait()
        assert stopped.is_set()


@asynccontextmanager
async def _client(
    tmp_path: Path,
    service: CanonWebSocketRouter,
    *,
    unified: bool = False,
    options: dict[str, Any] | None = None,
) -> AsyncIterator[Any]:
    package = "stream_integration_client"
    app = FastAPI()
    app.include_router(service)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, ws="websockets-sansio", log_level="error")
        )
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("The test server stopped before startup")
                await asyncio.sleep(0)
            document = service.contract(
                title="Streams", base_url=f"ws://127.0.0.1:{port}"
            ).to_openrpc()
            (tmp_path / "openrpc.json").write_text(
                json.dumps(document), encoding="utf-8"
            )
            if unified:
                files = render(
                    build_plan(((SourceConfig("rpc", "openrpc"), document),)),
                    TargetConfig(
                        "python", "python", ("rpc",), tmp_path, package=package
                    ),
                )
                for name, content in files.items():
                    path = tmp_path / package / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
            else:
                generate_python_client(
                    document,
                    tmp_path / package,
                    PythonClientOptions(
                        package=package,
                        client_name="StreamsClient",
                        with_transport="websocket",
                    ),
                )
            sys.path.insert(0, str(tmp_path))
            importlib.invalidate_caches()
            try:
                module = importlib.import_module(package)
                connection = (
                    module.ApiClient(
                        f"http://127.0.0.1:{port}", bindings={"rpc": options or {}}
                    )
                    if unified
                    else module.StreamsClient.connect(**(options or {}))
                )
                async with connection as client:
                    yield client
            finally:
                sys.path.remove(str(tmp_path))
                for name in tuple(sys.modules):
                    if name == package or name.startswith(package + "."):
                        del sys.modules[name]
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, 2)
