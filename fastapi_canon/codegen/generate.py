import difflib
import hashlib
import io
import json
import shutil
import subprocess
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from fastapi_canon._version import __version__
from fastapi_canon.application_loading import load_application
from fastapi_canon.codegen.config import Configuration, TargetConfig
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
    filenames = ("openapi", "openrpc", "webrtc")
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
            documents = export_contracts(app).documents
        contracts = {
            config.contracts / f"{kind}.json": _json(documents[kind])
            if kind in documents
            else ""
            for kind in filenames
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
) -> tuple[dict[str, str], Callable[..., tuple[Path, ...]]]:
    assert target.output is not None
    kind = {"sse": "openapi", "websockets": "openrpc", "webrtc": "webrtc"}[
        target.generator
    ]
    if kind not in documents:
        raise ValueError(f"Target {target.name} needs a registered {kind} contract")
    document = documents[kind]
    match target.generator:
        case "sse":
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

            diagnostics: list[GenerationDiagnostic] = []
            contract = build_ir(
                document,
                target=target.language,
                diagnostics=diagnostics,
                include_operations=target.include_operations,
            )
            generation = {
                "generator": "fastapi-canon",
                "generator_version": __version__,
                "schema_sha256": hashlib.sha256(
                    json.dumps(
                        document,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
                "schema_hash_encoding": "canonical-json-v1",
                "target": target.language,
                "python_http_client": target.http_client
                if target.language == "python"
                else None,
            }
            if target.diagnostics is not None:
                write_diagnostics(target.diagnostics, diagnostics, set(), generation)
            accepted = (
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
                    f"{len(pending)} unaccepted SSE generation diagnostics for {target.name}; review diagnostics and explicit allowances"
                )
            generated = (
                generate_python(contract, http_client=target.http_client)
                if target.language == "python"
                else generate_typescript(contract)
            )
            return planned_files(generated, generation=generation), partial(
                write_files, target.output, generated, generation=generation
            )
        case "websockets":
            from fastapi_canon.integrations.websockets.codegen import (
                PythonClientOptions,
                TypeScriptClientOptions,
                render_python_client,
                render_typescript_client,
            )
            from fastapi_canon.integrations.websockets.codegen import (
                write_files as write_rpc,
            )

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
            return generated, partial(write_rpc, target.output, generated)
        case "webrtc":
            from fastapi_canon.integrations.webrtc.codegen.typescript import (
                generate_typescript as render_rtc,
            )
            from fastapi_canon.integrations.webrtc.codegen.writer import (
                write_files as write_rtc,
            )

            generated = render_rtc(document, client_name=target.client_name)
            # Let the specialized writer produce its own ownership manifest.
            manifest = ".fastapi-webrtc.json"
            expected = {**generated, manifest: _json({"files": sorted(generated)})}
            return expected, partial(write_rtc, target.output, generated)
        case _:
            raise ValueError("External targets do not render Canon clients")


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
