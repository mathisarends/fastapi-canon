import keyword
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

type Generator = Literal["sse", "websockets", "webrtc", "external"]


@dataclass(frozen=True)
class TargetConfig:
    name: str
    generator: Generator
    language: Literal["python", "typescript"] = "typescript"
    output: Path | None = None
    package: str = "api_client"
    client_name: str = "ApiClient"
    http_client: Literal["httpx", "httpx2"] = "httpx"
    generate_command: tuple[str, ...] = ()
    check_command: tuple[str, ...] = ()
    timeout: int = 120
    strict: bool = False
    diagnostics: Path | None = None
    allow_diagnostics: Path | None = None
    include_operations: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Configuration:
    path: Path
    app: str
    contracts: Path
    targets: tuple[TargetConfig, ...]


def load_config(path: Path) -> Configuration:
    path = path.resolve()
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    raw = (
        document.get("tool", {}).get("fastapi-canon", {})
        if path.name == "pyproject.toml"
        else document
    )
    raw = raw.get("codegen", {})
    _keys(raw, {"version", "app", "contracts", "targets"}, "codegen")
    if raw.get("version") != 2:
        raise ValueError(
            "Use codegen version = 2; version 1 domain SDK generation was removed. See docs/codegen.md for migration."
        )
    app = raw.get("app")
    if not isinstance(app, str) or not app:
        raise ValueError(
            "codegen.app must name an application or zero-argument factory"
        )
    root = path.parent
    contracts = _path(root, raw.get("contracts", "contracts"))
    values = raw.get("targets")
    if not isinstance(values, dict) or not values:
        raise ValueError("codegen.targets must contain at least one target")
    targets = tuple(_target(root, name, value) for name, value in values.items())
    outputs = [target.output for target in targets if target.output is not None]
    for index, output in enumerate(outputs):
        for other in [contracts, *outputs[index + 1 :]]:
            if (
                output == other
                or output.is_relative_to(other)
                or other.is_relative_to(output)
            ):
                raise ValueError(
                    "Contract and native client output directories must not overlap"
                )
    reports = [
        target.diagnostics for target in targets if target.diagnostics is not None
    ]
    inputs = {
        path,
        *(target.allow_diagnostics for target in targets if target.allow_diagnostics),
    }
    for report in reports:
        if (
            report in inputs
            or reports.count(report) > 1
            or any(
                report == directory or report.is_relative_to(directory)
                for directory in (contracts, *outputs)
            )
        ):
            raise ValueError(
                "Diagnostics reports must be distinct and outside inputs and generated outputs"
            )
    return Configuration(path, app, contracts, targets)


def _target(root: Path, name: str, value: object) -> TargetConfig:
    if not name.isidentifier():
        raise ValueError("Target names must be identifiers")
    if not isinstance(value, dict):
        raise ValueError(f"Target {name} must be a table")
    generator = value.get("generator")
    if not isinstance(generator, str) or generator not in {
        "sse",
        "websockets",
        "webrtc",
        "external",
    }:
        raise ValueError(f"Target {name} needs a supported generator")
    if generator == "external":
        _keys(
            value, {"generator", "generate-command", "check-command", "timeout"}, name
        )
        timeout = value.get("timeout", 120)
        if type(timeout) is not int or timeout <= 0:
            raise ValueError(f"Target {name} timeout must be a positive integer")
        return TargetConfig(
            name,
            "external",
            generate_command=_command(value.get("generate-command")),
            check_command=_command(value.get("check-command")),
            timeout=timeout,
        )
    _keys(
        value,
        {
            "generator",
            "language",
            "output",
            "package",
            "client-name",
            "python-http-client",
            "strict",
            "diagnostics",
            "allow-diagnostics",
            "include-operations",
        },
        name,
    )
    language = value.get("language", "typescript")
    sse_keys = {"strict", "diagnostics", "allow-diagnostics", "include-operations"}
    if generator != "sse" and value.keys() & sse_keys:
        raise ValueError(
            "SSE diagnostic and operation options apply only to SSE targets"
        )
    strict = value.get("strict", False)
    if type(strict) is not bool:
        raise ValueError(f"Target {name} strict must be a boolean")
    include = value.get("include-operations")
    if "include-operations" in value and (
        not isinstance(include, list)
        or not include
        or any(not isinstance(item, str) or not item for item in include)
        or len(set(include)) != len(include)
    ):
        raise ValueError(
            f"Target {name} include-operations must contain unique nonempty IDs"
        )
    if language not in {"python", "typescript"} or (
        generator == "webrtc" and language != "typescript"
    ):
        raise ValueError(f"Unsupported language for target {name}")
    if "package" in value and (generator != "websockets" or language != "python"):
        raise ValueError("package applies only to Python WebSocket targets")
    if "client-name" in value and generator == "sse":
        raise ValueError("SSE client naming is owned by its specialized generator")
    http = value.get("python-http-client", "httpx")
    if http not in {"httpx", "httpx2"} or (
        "python-http-client" in value and (generator != "sse" or language != "python")
    ):
        raise ValueError("python-http-client applies only to Python SSE targets")
    package = value.get("package", "api_client")
    client = value.get("client-name", "ApiClient")
    if (
        not isinstance(package, str)
        or not all(part.isidentifier() for part in package.split("."))
        or not isinstance(client, str)
        or not client.isidentifier()
        or keyword.iskeyword(client)
        or any(keyword.iskeyword(part) for part in package.split("."))
    ):
        raise ValueError(f"Invalid package or client-name for target {name}")
    return TargetConfig(
        name,
        cast(Generator, generator),
        cast(Literal["python", "typescript"], language),
        _path(root, value.get("output")),
        package,
        client,
        cast(Literal["httpx", "httpx2"], http),
        strict=strict,
        diagnostics=_path(root, value["diagnostics"])
        if "diagnostics" in value
        else None,
        allow_diagnostics=_path(root, value["allow-diagnostics"])
        if "allow-diagnostics" in value
        else None,
        include_operations=tuple(include) if include is not None else None,
    )


def _path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Output paths must be nonempty strings")
    candidate = root / value
    if any(part.is_symlink() for part in (candidate, *candidate.parents)):
        raise ValueError("Output paths must not traverse symlinks")
    path = candidate.resolve()
    if path == root:
        raise ValueError("Use a dedicated output directory")
    return path


def _command(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(part, str) and part for part in value)
    ):
        raise ValueError(
            "External targets need nonempty generate-command and check-command argument arrays"
        )
    return tuple(value)


def _keys(value: object, allowed: set[str], owner: str) -> None:
    if not isinstance(value, dict) or value.keys() - allowed:
        raise ValueError(f"Invalid or unknown settings in {owner}")
