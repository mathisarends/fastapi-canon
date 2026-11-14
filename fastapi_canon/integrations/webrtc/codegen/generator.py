"""The ``webrtc`` client generator for ``fastapi-canon generate``."""

import json
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

from fastapi_canon.codegen.generators import Rendering, TargetConfig

__all__ = ["GENERATOR", "WebRtcGenerator"]


class WebRtcGenerator:
    """Render the typed TypeScript browser client from a WebRTC contract."""

    contract = "webrtc"
    settings: Mapping[str, frozenset[str]] = {"typescript": frozenset({"client-name"})}

    def render(
        self, target: TargetConfig, document: dict[str, Any], contracts: Path
    ) -> Rendering:
        """Render *target* with the WebRTC writer's ownership manifest."""
        from fastapi_canon.integrations.webrtc.codegen.typescript import (
            generate_typescript,
        )
        from fastapi_canon.integrations.webrtc.codegen.writer import write_files

        assert target.output is not None
        generated = generate_typescript(document, client_name=target.client_name)
        manifest = json.dumps(
            {"files": sorted(generated)},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        return Rendering(
            {**generated, ".fastapi-webrtc.json": manifest + "\n"},
            partial(write_files, target.output, generated),
        )


GENERATOR = WebRtcGenerator()
