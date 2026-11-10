import asyncio
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from starlette.types import ASGIApp, Receive, Scope, Send

from examples.canonical_errors.app import create_app
from tests.unit.integrations.webrtc.server import serve


@dataclass
class Session:
    refreshed: bool = False
    refreshes: int = 0


class Admission:
    def __init__(self, app: ASGIApp, session: Session) -> None:
        self.app = app
        self.session = session

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket" and not self.session.refreshed:
            await receive()
            await send(
                {
                    "type": "websocket.close",
                    "code": 1008,
                    "reason": "credentials_expired",
                }
            )
            return
        await self.app(scope, receive, send)


async def test_migration_consumers_run_against_real_http_sse_and_rpc(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[3]
    node = shutil.which("node")
    compiler = root / "node_modules/typescript/bin/tsc"
    if node is None or not compiler.is_file():
        pytest.skip("Install Node and the root pnpm dependencies")
    example = tmp_path / "migration_example"
    example.mkdir()
    for name in (
        "__init__.py",
        "app.py",
        "consumer.py",
        "consumer.ts",
        "tsconfig.json",
        "fastapi-canon.toml",
        "streams-python-allowances.json",
        "streams-typescript-allowances.json",
    ):
        (example / name).write_bytes(
            (root / "examples/canonical_errors" / name).read_bytes()
        )
    generated = await _run([sys.executable, "-m", "fastapi_canon", "generate"], example)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    checked = await _run(
        [sys.executable, "-m", "fastapi_canon", "generate", "--check"], example
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    compiled = await _run(
        [node, str(compiler), "-p", str(example / "tsconfig.json")], tmp_path
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    python = tmp_path / "consumer_check.py"
    python.write_text(
        """import asyncio, json, sys
from httpx2 import AsyncClient
from migration_example.consumer import read_events
from migration_example.dist.streams.python import ProblemError
async def main():
    base = sys.argv[1]
    async with AsyncClient() as http:
        assert await read_events(base, http, 1, limit=1) == [1]
        assert not http.is_closed
        problems = []
        for project in (0, 2):
            try:
                await read_events(base, http, project)
                raise AssertionError("expected a problem")
            except ProblemError as error:
                problems.append(error.problem.code)
        assert (await http.get(base + "/projects/1")).status_code == 200
        assert not http.is_closed
    assert http.is_closed
    print(json.dumps(problems))
asyncio.run(main())
""",
        encoding="utf-8",
    )
    javascript = tmp_path / "consumer_check.cjs"
    javascript.write_text(
        """const assert = require("node:assert/strict");
const { openProjectsRpc, readEvents, problemOf } = require("./migration_example/dist/consumer/consumer.js");
const { ProblemError: StreamProblemError } = require("./migration_example/dist/consumer/dist/streams/typescript/index.js");
(async () => {
  const base = process.argv[2];
  const signal = AbortSignal.timeout(10000);
  assert.deepEqual(await readEvents(base, 1, signal), [1]);
  for (const id of [0, 2]) {
    await assert.rejects(readEvents(base, id, signal), error => problemOf(error)?.code === "project_missing");
  }
  let refreshes = 0;
  const refreshSession = async () => {
    refreshes++;
    const response = await fetch(base + "/refresh", { method: "POST", signal });
    assert.equal(response.status, 200);
  };
  const first = await openProjectsRpc(base, { refreshSession, signal });
  const second = await openProjectsRpc(base, { refreshSession, signal });
  try {
    await assert.rejects(first.projects.cancel({ project_id: 0 }), error => {
      assert.equal(error instanceof StreamProblemError, false);
      assert.equal(problemOf(error)?.type, "https://api.example.com/problems/project_missing");
      return true;
    });
    await first.close();
    await second.projects.cancel({ project_id: 1 });
    assert.equal(refreshes, 1);
    const controller = new AbortController();
    controller.abort(new Error("cancelled"));
    await assert.rejects(openProjectsRpc(base, { refreshSession, signal: controller.signal }), /cancelled/);
    assert.equal(refreshes, 1);
  } finally {
    await first.close();
    await second.close();
  }
  console.log(JSON.stringify({ refreshed: refreshes, independentConnections: true }));
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
        encoding="utf-8",
    )
    app = create_app()
    session = Session()
    app.add_middleware(Admission, session=session)

    @app.post("/refresh")
    async def refresh() -> bool:
        session.refreshed = True
        session.refreshes += 1
        return True

    async with asyncio.timeout(45), serve(app) as base:
        base = base.replace("ws://", "http://")
        python_result = await _run([sys.executable, str(python), base], tmp_path)
        assert python_result.returncode == 0, (
            python_result.stdout + python_result.stderr
        )
        assert json.loads(python_result.stdout) == [
            "project_missing",
            "project_missing",
        ]
        result = await _run([node, str(javascript), base], tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == {
            "refreshed": 1,
            "independentConnections": True,
        }
    assert session.refreshes == 1


async def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(
        subprocess.run,
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
