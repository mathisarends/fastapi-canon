import importlib
import shutil
import subprocess
import sys
from collections.abc import Iterator
from copy import deepcopy
from textwrap import dedent
from types import ModuleType
from typing import Any

import pytest
from fastapi_canon.integrations.websockets.codegen import (
    generate_python_client,
    generate_typescript_client,
)
from fastapi_canon.integrations.websockets.codegen.python import PythonClientOptions
from fastapi_canon.integrations.websockets.codegen.typescript import (
    TypeScriptClientOptions,
)
from fastapi_canon.integrations.websockets.schema import render_openrpc

from tests.unit.integrations.websockets.conftest import greeting_app

PACKAGE = "greeting_client"


@pytest.fixture(scope="session")
def document() -> dict[str, Any]:
    return render_openrpc(greeting_app.protocol, title="Greeting")


@pytest.fixture(scope="session")
def options() -> PythonClientOptions:
    return PythonClientOptions(
        package=PACKAGE,
        client_name="GreetingClient",
        source="greeting.openrpc.json",
    )


@pytest.fixture(scope="session")
def generated_client(
    document: dict[str, Any],
    options: PythonClientOptions,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ModuleType]:
    root = tmp_path_factory.mktemp("generated")
    generate_python_client(document, root / PACKAGE, options)
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    try:
        yield importlib.import_module(PACKAGE)
    finally:
        sys.path.remove(str(root))
        for name in [
            name
            for name in sys.modules
            if name == PACKAGE or name.startswith(f"{PACKAGE}.")
        ]:
            del sys.modules[name]


# All consumers execute in fresh Node processes; only compiled files are shared.
@pytest.fixture(scope="session")
def compiled_typescript_sdk(document, tmp_path_factory):
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    node = shutil.which("node.exe") or shutil.which("node")
    if pnpm is None or node is None:
        pytest.skip("Node.js and pnpm are required")
    root = tmp_path_factory.mktemp("typescript_cancellation")
    deployed = deepcopy(document)
    deployed["servers"] = [
        {
            "name": "rpc",
            "url": "ws://test",
            "x-rpckit-transport": {"type": "websocket", "messageEncoding": "json"},
        }
    ]
    generate_typescript_client(
        deployed,
        root / "generated",
        TypeScriptClientOptions(
            with_transport="websocket", client_name="GreetingClient"
        ),
    )
    (root / "types.ts").write_text(
        dedent("""
        import { GreetingClient, type RpcRequestOptions } from "./generated";
        import { RpcClientCore, defineRoute } from "./generated/core";
        const oldTransport = {
          async request(_method: string, _params?: object): Promise<unknown> {
            return {};
          },
          async *notifications() {},
          async close() {},
        };
        const client = new GreetingClient(oldTransport);
        const options: RpcRequestOptions = { signal: new AbortController().signal };
        void client.greeting.say({ name: "Ada" }, options);
        void client.greeting.greeted(options);
        void client.greeting.say({ name: "Ada" });
        void client.greeting.greeted();
        const core = new RpcClientCore(oldTransport);
        void core.request(
          defineRoute<undefined, string>({ method: "ping" }),
          undefined,
          options,
        );
        // @ts-expect-error signals are AbortSignal instances
        void client.greeting.greeted({ signal: "invalid" });
        // @ts-expect-error required method params are still required
        void client.greeting.say(options);
    """),
        encoding="utf-8",
    )
    checked = subprocess.run(
        [
            pnpm,
            "exec",
            "tsc",
            "--strict",
            "--target",
            "ES2021",
            "--module",
            "Node16",
            "--moduleResolution",
            "Node16",
            "--lib",
            "ES2021,DOM,ESNext.Disposable",
            "--skipLibCheck",
            "--outDir",
            str(root / "compiled"),
            str(root / "types.ts"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    return root, node
