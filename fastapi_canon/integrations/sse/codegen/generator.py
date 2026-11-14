"""The ``sse`` client generator for ``fastapi-canon generate``."""

import hashlib
import json
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

from fastapi_canon.codegen.generators import Rendering, TargetConfig

__all__ = ["GENERATOR", "SseGenerator"]

_DIAGNOSTICS = frozenset(
    {"strict", "diagnostics", "allow-diagnostics", "include-operations"}
)


class SseGenerator:
    """Render typed Python or TypeScript SSE clients from OpenAPI."""

    contract = "openapi"
    settings: Mapping[str, frozenset[str]] = {
        "typescript": _DIAGNOSTICS,
        "python": _DIAGNOSTICS | {"python-http-client"},
    }

    def render(
        self,
        target: TargetConfig,
        document: dict[str, Any],
        # Required by the Generator protocol; SSE renders from *document* alone.
        contracts: Path,  # pylint: disable=unused-argument
    ) -> Rendering:
        """Render *target*, writing its diagnostics report when configured."""
        from fastapi_canon._version import __version__
        from fastapi_canon.integrations.sse.codegen.diagnostics import (
            GenerationDiagnostic,
            accepted_diagnostics,
            emit_diagnostics,
            write_diagnostics,
        )
        from fastapi_canon.integrations.sse.codegen.ir import build_ir
        from fastapi_canon.integrations.sse.codegen.python import generate_python
        from fastapi_canon.integrations.sse.codegen.typescript import (
            generate_typescript,
        )
        from fastapi_canon.integrations.sse.codegen.writer import (
            planned_files,
            write_files,
        )

        assert target.output is not None
        language = "python" if target.language == "python" else "typescript"
        diagnostics: list[GenerationDiagnostic] = []
        contract = build_ir(
            document,
            target=language,
            diagnostics=diagnostics,
            include_operations=target.include_operations,
        )
        generation = {
            "generator": "fastapi-canon",
            "generator_version": __version__,
            "schema_sha256": hashlib.sha256(
                json.dumps(
                    document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest(),
            "schema_hash_encoding": "canonical-json-v1",
            "target": language,
            "python_http_client": target.http_client if language == "python" else None,
        }
        if target.diagnostics is not None:
            write_diagnostics(target.diagnostics, diagnostics, set(), generation)
        accepted: set[str] = (
            accepted_diagnostics(
                diagnostics,
                json.loads(target.allow_diagnostics.read_text(encoding="utf-8")),
            )
            if target.allow_diagnostics
            else set()
        )
        if target.diagnostics is not None:
            write_diagnostics(target.diagnostics, diagnostics, accepted, generation)
        pending = [item for item in diagnostics if item.id not in accepted]
        emit_diagnostics(pending)
        if target.strict and pending:
            raise ValueError(
                f"{len(pending)} unaccepted SSE generation diagnostics for "
                f"{target.name}; review diagnostics and explicit allowances"
            )
        generated = (
            generate_python(contract, http_client=target.http_client)
            if language == "python"
            else generate_typescript(contract)
        )
        return Rendering(
            planned_files(generated, generation=generation),
            partial(write_files, target.output, generated, generation=generation),
        )


GENERATOR = SseGenerator()
