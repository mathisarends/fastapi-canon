"""The ``websockets`` client generator for ``fastapi-canon generate``."""

from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

from fastapi_canon.codegen.generators import Rendering, TargetConfig

__all__ = ["GENERATOR", "WebSocketGenerator"]


class WebSocketGenerator:
    """Render typed Python or TypeScript JSON-RPC clients from OpenRPC."""

    contract = "openrpc"
    settings: Mapping[str, frozenset[str]] = {
        "typescript": frozenset({"client-name"}),
        "python": frozenset({"client-name", "package"}),
    }

    def render(
        self, target: TargetConfig, document: dict[str, Any], contracts: Path
    ) -> Rendering:
        """Render *target* against the saved ``openrpc.json`` contract."""
        from fastapi_canon.integrations.websockets.codegen import (
            PythonClientOptions,
            TypeScriptClientOptions,
            render_python_client,
            render_typescript_client,
            write_files,
        )

        assert target.output is not None
        source = contracts / "openrpc.json"
        generated = (
            dict(
                render_python_client(
                    document,
                    PythonClientOptions(
                        package=target.package,
                        client_name=target.client_name,
                        with_transport="websocket",
                        source=source.name,
                        contract=source,
                    ),
                )
            )
            if target.language == "python"
            else dict(
                render_typescript_client(
                    document,
                    TypeScriptClientOptions(
                        client_name=target.client_name,
                        with_transport="websocket",
                        source=source.name,
                        contract=source,
                    ),
                )
            )
        )
        return Rendering(generated, partial(write_files, target.output, generated))


GENERATOR = WebSocketGenerator()
