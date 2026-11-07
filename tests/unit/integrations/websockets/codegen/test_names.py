import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi_canon.integrations.websockets.codegen import (
    render_python_client,
    render_typescript_client,
)
from fastapi_canon.integrations.websockets.codegen.ir import UnsupportedSchemaError
from fastapi_canon.integrations.websockets.codegen.names import (
    camel_case,
    pascal_case,
    snake_case,
)
from fastapi_canon.integrations.websockets.codegen.python import PythonClientOptions
from fastapi_canon.integrations.websockets.codegen.typescript import (
    TypeScriptClientOptions,
)


def test_identifier_styles_handle_acronyms_stably() -> None:
    assert snake_case("parseURL") == "parse_url"
    assert snake_case("HTTPStatus") == "http_status"
    assert camel_case("parseURL") == "parseUrl"
    assert pascal_case("browser-nav") == "BrowserNav"


def test_typescript_connection_signal_cannot_collide_with_a_url_variable(
    document: dict[str, Any],
) -> None:
    deployed = deepcopy(document)
    deployed["servers"] = [
        {
            "name": "primary",
            "url": "wss://example.test/{signal}",
            "variables": {"signal": {"default": "project"}},
            "x-rpckit-transport": {"type": "websocket", "messageEncoding": "json"},
        }
    ]
    with pytest.raises(UnsupportedSchemaError, match="connect options"):
        render_typescript_client(
            deployed, TypeScriptClientOptions(with_transport="websocket")
        )


def test_typescript_resolves_wire_server_names_and_preserves_public_helpers(
    document: dict[str, Any],
    tmp_path: Path,
) -> None:
    deployed = deepcopy(document)
    deployed["servers"] = [
        {
            "name": name,
            "url": f"wss://example.test/{{project_id}}/{name}",
            "variables": {"project_id": {"default": "42"}},
        }
        for name in (
            "primary",
            "project_session",
            "task-session",
            "rpc_projects_project_id_session",
        )
    ]
    files = render_typescript_client(deployed, TypeScriptClientOptions())
    endpoint_source = tmp_path / "endpoints.ts"
    endpoint_source.write_text(files["endpoints.ts"], encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type":"commonjs"}', encoding="utf-8")
    node = shutil.which("node")
    compiler = Path(__file__).resolve().parents[5] / "node_modules/typescript/bin/tsc"
    if node is None or not compiler.is_file():
        pytest.skip("Node.js and the installed TypeScript compiler are required")
    compiled = subprocess.run(
        [
            node,
            str(compiler),
            "--strict",
            "--target",
            "ES2022",
            "--module",
            "commonjs",
            "--lib",
            "ES2022,DOM",
            "--outDir",
            str(tmp_path / "js"),
            str(endpoint_source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    runner = tmp_path / "check.cjs"
    runner.write_text(
        'const {resolveEndpoints, servers, endpoints} = require("./js/endpoints.js");\n'
        "console.log(JSON.stringify({\n"
        "  resolved: Object.fromEntries(\n"
        '    resolveEndpoints({project_id: "42"}).map(e => [e.server, e.url])),\n'
        "  publicName: servers.projectSession.name,\n"
        '  helperName: endpoints.projectSession({project_id: "42"}).server,\n'
        "}));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(runner)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "resolved": {
            "primary": "wss://example.test/42/primary",
            "project_session": "wss://example.test/42/project_session",
            "task-session": "wss://example.test/42/task-session",
            "rpc_projects_project_id_session": "wss://example.test/42/rpc_projects_project_id_session",
        },
        "publicName": "project_session",
        "helperName": "project_session",
    }


def test_api_aliases_cannot_merge_distinct_wire_paths(
    document: dict[str, Any],
) -> None:
    collision = deepcopy(document)
    first = collision["methods"][0]
    second = deepcopy(first)
    first["name"] = "browser.nav.tabs.open"
    second["name"] = "browser.navigation.history.close"
    collision["methods"] = [first, second]

    with pytest.raises(UnsupportedSchemaError, match="both map to 'navigation'"):
        render_python_client(
            collision,
            PythonClientOptions(
                package="browser_client",
                api_root="browser",
                api_names={"nav": "navigation"},
            ),
        )


@pytest.mark.parametrize("language", ["python", "typescript"])
@pytest.mark.parametrize(
    "name", ["close", "closed", "state", "states", "droppedNotifications"]
)
def test_root_operations_cannot_shadow_client_lifecycle(
    document: dict[str, Any],
    language,
    name,
) -> None:
    collision = deepcopy(document)
    collision["methods"][0]["name"] = (
        "dropped_notifications"
        if language == "python" and name == "droppedNotifications"
        else name
    )
    collision["methods"] = [collision["methods"][0]]

    with pytest.raises(UnsupportedSchemaError, match="root client"):
        if language == "python":
            render_python_client(collision, PythonClientOptions(package="client"))
        else:
            render_typescript_client(collision, TypeScriptClientOptions())


@pytest.mark.parametrize("language", ["python", "typescript"])
@pytest.mark.parametrize(
    "name",
    [
        "RpcConnectError",
        "RpcConnectionLostError",
        "RpcRequestTimeoutError",
        "RpcConnectionState",
        "RpcNotificationOverflowHandler",
    ],
)
def test_sdk_error_names_cannot_be_shadowed_by_models(language, name):
    document = {
        "openrpc": "1.4.1",
        "info": {"title": "Collision", "version": "1"},
        "components": {
            "schemas": {
                name: {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                }
            }
        },
        "methods": [
            {
                "name": "ping",
                "params": [],
                "result": {
                    "name": "result",
                    "schema": {"$ref": f"#/components/schemas/{name}"},
                },
            }
        ],
    }
    with pytest.raises(UnsupportedSchemaError):
        if language == "python":
            render_python_client(
                document, PythonClientOptions(package="collision_client")
            )
        else:
            render_typescript_client(document, TypeScriptClientOptions())
