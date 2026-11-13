"""Deterministic generated-file output owned by a manifest."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi_canon.codegen.writer import write_generated_files

MANIFEST = ".fastapi-webrtc.json"


def write_files(
    output: Path,
    files: Mapping[str, str],
    *,
    check: bool = False,
    generation: Mapping[str, str | None] | None = None,
) -> tuple[Path, ...]:
    """Write deterministic output; only remove stale files owned by the manifest.

    Check mode is read-only, including when output does not yet exist.
    """
    expected = dict(files)
    if MANIFEST in expected:
        raise ValueError(f"{MANIFEST} is reserved")
    metadata: dict[str, Any] = {"files": sorted(files)}
    if generation is not None:
        metadata["generation"] = dict(generation)
    expected[MANIFEST] = json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    return write_generated_files(output, expected, manifest=MANIFEST, check=check)
