import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi_canon.codegen.writer import write_generated_files

MANIFEST = ".fastapi-canon-sse.json"
_LEGACY_MANIFEST = ".fastapi-streams.json"


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
    expected = planned_files(files, generation=generation)
    return write_generated_files(
        output,
        expected,
        manifest=MANIFEST,
        legacy_manifests=(_LEGACY_MANIFEST,),
        check=check,
    )


def planned_files(
    files: Mapping[str, str],
    *,
    generation: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    """Return the generated files plus the ownership manifest, keyed by path."""
    expected = dict(files)
    for name in (MANIFEST, _LEGACY_MANIFEST):
        if name in expected:
            raise ValueError(f"{name} is reserved")
    metadata: dict[str, Any] = {"files": sorted(files)}
    if generation is not None:
        metadata["generation"] = dict(generation)
    expected[MANIFEST] = json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    return expected
