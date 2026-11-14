import difflib
import io
import json
import shutil
import subprocess
from collections.abc import Callable, Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi_canon.application_loading import load_application
from fastapi_canon.codegen.config import Configuration
from fastapi_canon.codegen.generators import (
    TargetConfig,
    contract_kinds,
    load_generator,
)
from fastapi_canon.contracts import export_contracts


@dataclass(frozen=True)
class StepResult:
    name: str
    status: str
    changes: tuple[str, ...] = ()
    diff: str = ""
    output: str = ""


def generate(
    config: Configuration,
    *,
    targets: tuple[str, ...] = (),
    offline: bool = False,
    check: bool = False,
    diff: bool = False,
) -> tuple[StepResult, ...]:
    check = check or diff
    selected = tuple(
        target for target in config.targets if not targets or target.name in targets
    )
    if not selected or set(targets) - {target.name for target in selected}:
        raise ValueError("Unknown generation target")
    root = config.path.parent
    filenames = contract_kinds()
    if offline:
        documents = {
            kind: json.loads(path.read_text(encoding="utf-8"))
            for kind in filenames
            if (path := config.contracts / f"{kind}.json").is_file()
        }
        contracts: dict[Path, str] = {}
    else:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            app = load_application(config.app, root=root)
            documents = dict(export_contracts(app).documents)
        contracts = {
            config.contracts / f"{kind}.json": _json(documents[kind])
            if kind in documents
            else ""
            for kind in sorted(filenames | documents.keys())
        }
    documents = {
        kind: json.loads(_json(document)) for kind, document in documents.items()
    }
    changed = tuple(path for path, text in contracts.items() if _changed(path, text))
    contract_result = StepResult(
        "contracts",
        "drift" if check and changed else "updated" if changed else "current",
        tuple(str(path) for path in changed),
        _diff(contracts, changed) if diff else "",
    )
    if check and changed:
        return (
            contract_result,
            *(
                StepResult(
                    target.name,
                    "blocked",
                    output="Exported contracts are stale; regenerate before checking dependent clients.",
                )
                for target in selected
            ),
        )
    # Render every native target and validate all owned paths before writing contracts.
    prepared = []
    for target in selected:
        if target.generator != "external":
            expected, writer = _render(target, documents, config.contracts)
            assert target.output is not None
            for name in expected:
                _regular(target.output / name)
            paths = writer(check=True)
            prepared.append((target, expected, writer, paths))
    for path in contracts:
        _regular(path)
    if not check:
        for path in changed:
            if contracts[path]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contracts[path], encoding="utf-8", newline="\n")
            else:
                path.unlink()
    results = [contract_result]
    for target in selected:
        if target.generator == "external":
            results.append(_external(target, root, check=check))
            continue
        _, expected, writer, paths = next(
            item for item in prepared if item[0] == target
        )
        assert target.output is not None
        report = (
            _diff(
                {target.output / name: text for name, text in expected.items()}, paths
            )
            if diff
            else ""
        )
        if not check:
            writer(check=False)
        results.append(
            StepResult(
                target.name,
                "drift" if check and paths else "updated" if paths else "current",
                tuple(str(path) for path in paths),
                report,
            )
        )
    return tuple(results)


def _render(
    target: TargetConfig, documents: dict[str, Any], contracts: Path
) -> tuple[Mapping[str, str], Callable[..., tuple[Path, ...]]]:
    generator = load_generator(target.generator)
    if generator.contract not in documents:
        raise ValueError(
            f"Target {target.name} needs a registered {generator.contract} contract"
        )
    rendering = generator.render(target, documents[generator.contract], contracts)
    return rendering.files, rendering.write


def _external(target: TargetConfig, root: Path, *, check: bool) -> StepResult:
    command = target.check_command if check else target.generate_command
    executable = shutil.which(command[0])
    if executable is None:
        return StepResult(
            target.name, "error", output=f"Executable not found: {command[0]}"
        )
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=target.timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return StepResult(
            target.name,
            "error",
            output=f"External command failed ({type(error).__name__})",
        )
    status = (
        "current"
        if check and result.returncode == 0
        else "success"
        if result.returncode == 0
        else "drift"
        if check and result.returncode == 1
        else "error"
    )
    return StepResult(target.name, status, output=result.stdout + result.stderr)


def _json(value: Any) -> str:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    )


def _changed(path: Path, expected: str) -> bool:
    _regular(path)
    if not expected:
        return path.exists()
    return not path.is_file() or path.read_bytes() != expected.encode("utf-8")


def _regular(path: Path) -> None:
    if any(parent.is_symlink() for parent in (path, *path.parents)) or (
        path.exists() and not path.is_file()
    ):
        raise ValueError(
            f"Generated output must be a regular file without symlinks: {path}"
        )


def _diff(expected: dict[Path, str], changed: tuple[Path, ...]) -> str:
    return "".join(
        "".join(
            difflib.unified_diff(
                (path.read_text(encoding="utf-8") if path.is_file() else "").splitlines(
                    True
                ),
                expected.get(path, "").splitlines(True),
                fromfile=str(path),
                tofile=str(path),
            )
        )
        for path in changed
    )
