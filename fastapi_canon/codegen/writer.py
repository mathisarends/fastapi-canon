import json
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath


def write_generated_files(
    output: Path,
    files: Mapping[str, str],
    *,
    manifest: str,
    legacy_manifests: tuple[str, ...] = (),
    check: bool = False,
    sort_files: bool = True,
    remove_empty_directories: bool = False,
) -> tuple[Path, ...]:
    """Preflight ownership, then atomically replace each changed generated file."""
    previous: set[str] = set()
    for name in (manifest, *legacy_manifests):
        path = _owned_path(output, name)
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise ValueError(f"Invalid generated manifest: {path}") from error
        names = value.get("files") if isinstance(value, dict) else None
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError(f"Invalid generated manifest: {path}")
        previous.update(names)
        if name in legacy_manifests:
            previous.add(name)
    paths = {name: _owned_path(output, name) for name in set(files) | previous}
    changed: list[Path] = []
    items = sorted(files.items()) if sort_files else files.items()
    for name, content in items:
        path = paths[name]
        if path.is_file() and path.read_bytes() == content.encode("utf-8"):
            continue
        changed.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, delete=False
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(content.encode("utf-8"))
                temporary.replace(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    for name in sorted(previous - set(files)):
        path = paths[name]
        if path.is_file():
            changed.append(path)
            if not check:
                path.unlink()
                if remove_empty_directories:
                    _remove_empty_parents(path.parent, output)
    return tuple(changed)


def _owned_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
        raise ValueError(f"Unsafe generated path: {relative!r}")
    resolved_root = root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(resolved_root) or resolved == resolved_root:
        raise ValueError(f"Generated path escapes output directory: {relative!r}")
    return resolved


def _remove_empty_parents(directory: Path, output: Path) -> None:
    root = output.resolve()
    while directory != root and directory.is_relative_to(root):
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent
