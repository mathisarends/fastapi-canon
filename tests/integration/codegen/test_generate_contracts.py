import json
import shutil
import subprocess
import sys
from pathlib import Path


def test_generation_exports_one_app_instance_and_offline_reuses_saved_contracts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "application.py"
    source.write_text(
        "from fastapi import FastAPI\n"
        "from fastapi_canon.integrations.websockets import CanonWebSocketRouter, RpcChannel\n"
        "calls = 0\n"
        "def create_app():\n"
        "    global calls\n"
        "    calls += 1\n"
        "    assert calls == 1, 'App factory called more than once'\n"
        "    channel = RpcChannel('control')\n"
        "    @channel.method()\n"
        "    async def ping() -> str: return 'pong'\n"
        "    router = CanonWebSocketRouter('/projects/{projectId}/sessions/{sessionId}/rpc', channels=[channel])\n"
        "    app = FastAPI()\n"
        "    app.include_router(router, prefix='/api/v1')\n"
        "    @app.get('/status', operation_id='system.status')\n"
        "    def status() -> str: return 'ok'\n"
        "    return app\n",
        encoding="utf-8",
    )
    (tmp_path / "fastapi-canon.toml").write_text(
        "[codegen]\nversion = 1\n"
        '[codegen.sources.api]\nkind = "openapi"\n'
        'source = "application:create_app"\nfactory = true\noutput = "contracts/openapi.json"\n'
        '[codegen.sources.control]\nkind = "openrpc"\n'
        'source = "application:create_app"\nfactory = true\noutput = "contracts/openrpc.json"\n'
        '[codegen.targets.python]\nlanguage = "python"\nsources = ["api", "control"]\n'
        'output = "sdk"\npackage = "sdk"\n'
        '[codegen.targets.browser]\nlanguage = "typescript"\nsources = ["api", "control"]\n'
        'output = "browser"\n',
        encoding="utf-8",
    )
    command = [sys.executable, "-m", "fastapi_canon", "generate"]
    generated = subprocess.run(
        command, cwd=tmp_path, text=True, capture_output=True, timeout=60
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    document = json.loads(
        (tmp_path / "contracts/openrpc.json").read_text(encoding="utf-8")
    )
    assert (
        document["servers"][0]["url"]
        == "{baseUrl}/api/v1/projects/{projectId}/sessions/{sessionId}/rpc"
    )
    exercised = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio
import socket
import uvicorn
from application import create_app
from sdk import ApiClient

async def exercise():
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level='error', lifespan='off'))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(15):
            while not server.started:
                if serving.done():
                    await serving
                    raise AssertionError('Server exited before starting')
                await asyncio.sleep(0.01)
            async with ApiClient(
                f'http://127.0.0.1:{port}',
                bindings={'control': {'project_id': 'project-1', 'session_id': 'session-1'}},
            ) as client:
                assert await client.control.ping() == 'pong'
                assert await client.system.status() == 'ok'
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        listener.close()

asyncio.run(exercise())
""",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert exercised.returncode == 0, exercised.stdout + exercised.stderr
    compiler = Path(__file__).resolve().parents[3] / "node_modules/typescript/bin/tsc"
    node = shutil.which("node")
    if node is not None and compiler.is_file():
        compiled = subprocess.run(
            [
                node,
                str(compiler),
                "--strict",
                "--noEmit",
                "--target",
                "ES2022",
                "--module",
                "NodeNext",
                "--moduleResolution",
                "NodeNext",
                "--lib",
                "ES2022,DOM,ESNext.Disposable",
                "browser/index.ts",
            ],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            timeout=45,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    source.write_text(
        "raise AssertionError('Offline generation imported the app')\n",
        encoding="utf-8",
    )
    checked = subprocess.run(
        [*command, "--offline", "--check", "--diff"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
