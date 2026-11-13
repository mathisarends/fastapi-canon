from collections.abc import Mapping
from pathlib import Path

from fastapi_canon.codegen.writer import write_generated_files

MANIFEST = ".rpcgen/manifest.json"


def write_files(
    output_dir: Path,
    files: Mapping[str, str],
    *,
    check: bool = False,
) -> tuple[Path, ...]:
    return write_generated_files(
        output_dir,
        files,
        manifest=MANIFEST,
        check=check,
        sort_files=False,
        remove_empty_directories=True,
    )
