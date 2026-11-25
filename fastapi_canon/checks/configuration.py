import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

type FailOn = Literal["error", "warning"]


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorCheckConfig:
    """The ``[checks.errors]`` table of one application."""

    roots: tuple[Path, ...]
    references: tuple[Path, ...]
    strict: bool
    require_complete: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class CompositionCheckConfig:
    """The ``[checks.composition]`` table of one application."""

    app: str
    require_complete: bool
    fail_on: FailOn


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplicationConfig:
    """The checks that one configuration file declares for its application."""

    name: str
    path: Path
    errors: ErrorCheckConfig | None
    composition: CompositionCheckConfig | None


def load_applications(paths: Sequence[Path]) -> tuple[ApplicationConfig, ...]:
    """Read one application per configuration file; names must be unique."""
    applications = tuple(_load(path) for path in paths)
    names = [application.name for application in applications]
    for name in names:
        if names.count(name) > 1:
            raise ValueError(
                f"duplicate application name {name!r}; set a distinct name in each configuration"
            )
    return applications


def _load(path: Path) -> ApplicationConfig:
    path = path.resolve()
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path}: {error}") from None
    raw = document
    if path.name == "pyproject.toml":
        raw = document.get("tool", {})
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: tool must be a table")
        raw = raw.get("fastapi-canon", {})
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: tool.fastapi-canon must be a table")
    name = raw.get("name", path.parent.name)
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path}: name must be a nonempty string")
    checks = raw.get("checks", {})
    _keys(checks, {"errors", "composition"}, owner="checks", path=path)
    errors = checks.get("errors")
    composition = checks.get("composition", {})
    _keys(
        composition,
        {"app", "require-complete", "fail-on"},
        owner="checks.composition",
        path=path,
    )
    codegen = raw.get("codegen", {})
    app = composition.get(
        "app", codegen.get("app") if isinstance(codegen, dict) else None
    )
    if app is not None and (not isinstance(app, str) or not app):
        raise ValueError(f"{path}: checks.composition.app must be a nonempty string")
    threshold = composition.get("fail-on", "error")
    if threshold not in ("error", "warning"):
        raise ValueError(f"{path}: checks.composition.fail-on must be error or warning")
    fail_on: FailOn = "warning" if threshold == "warning" else "error"
    return ApplicationConfig(
        name=name,
        path=path,
        errors=None if errors is None else _errors(errors, path),
        composition=None
        if app is None
        else CompositionCheckConfig(
            app=app,
            require_complete=_flag(composition, "require-complete", path),
            fail_on=fail_on,
        ),
    )


def _errors(value: object, path: Path) -> ErrorCheckConfig:
    _keys(
        value,
        {"roots", "reference-roots", "strict", "require-complete"},
        owner="checks.errors",
        path=path,
    )
    assert isinstance(value, dict)
    roots = _directories(value.get("roots"), "roots", path)
    if not roots:
        raise ValueError(f"{path}: checks.errors.roots must name at least one root")
    return ErrorCheckConfig(
        roots=roots,
        references=_directories(
            value.get("reference-roots", []), "reference-roots", path
        ),
        strict=_flag(value, "strict", path),
        require_complete=_flag(value, "require-complete", path),
    )


def _directories(value: object, key: str, path: Path) -> tuple[Path, ...]:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{path}: checks.errors.{key} must be unique nonempty strings")
    directories = tuple((path.parent / item).resolve() for item in value)
    for directory in directories:
        if not directory.is_dir():
            raise ValueError(
                f"{path}: checks.errors.{key} is not a directory: {directory}"
            )
    return directories


def _flag(table: dict[str, object], key: str, path: Path) -> bool:
    value = table.get(key, False)
    if type(value) is not bool:
        raise ValueError(f"{path}: {key} must be a boolean")
    return value


def _keys(value: object, allowed: set[str], *, owner: str, path: Path) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: {owner} must be a table")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(
            f"{path}: unknown settings in {owner}: {', '.join(sorted(unknown))}"
        )
