"""Immutable composition descriptions and system-check results."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class DependencyInfo:
    name: str
    origin: str


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    code: str
    type_uri: str | None
    status: int
    origin: str
    title: str = ""


@dataclass(frozen=True, slots=True)
class OperationInfo:
    feature_name: str
    operation_id: str | None
    path: str
    methods: tuple[str, ...]
    dependencies: tuple[DependencyInfo, ...] = ()
    errors: tuple[ErrorInfo, ...] = ()
    include_in_schema: bool = True

    def __post_init__(self) -> None:
        _freeze_sequences(self, "methods", "dependencies", "errors")


@dataclass(frozen=True, slots=True)
class FeatureInfo:
    name: str
    operations: tuple[OperationInfo, ...]
    providers: tuple[str, ...]

    def __post_init__(self) -> None:
        _freeze_sequences(self, "operations", "providers")


@dataclass(frozen=True, slots=True)
class CompositionInfo:
    features: tuple[FeatureInfo, ...]
    operations: tuple[OperationInfo, ...]

    def __post_init__(self) -> None:
        _freeze_sequences(self, "features", "operations")

    def operation(self, operation_id: str) -> OperationInfo:
        """Find exactly one operation; never silently choose an ambiguous ID."""
        matches = tuple(op for op in self.operations if op.operation_id == operation_id)
        if len(matches) != 1:
            raise LookupError(
                f"Expected one operation {operation_id!r}, found {len(matches)}"
            )
        return matches[0]


@dataclass(frozen=True, slots=True)
class CheckIssue:
    code: str
    message: str
    hint: str
    severity: Literal["error", "warning"] = "error"
    feature_names: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.severity not in {"error", "warning"}:
            raise ValueError("Check severity must be error or warning")
        _freeze_sequences(self, "feature_names", "locations")


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: Literal["passed", "failed", "incomplete", "skipped"]

    def __post_init__(self) -> None:
        if self.status not in {"passed", "failed", "incomplete", "skipped"}:
            raise ValueError("Invalid check status")


@dataclass(frozen=True, slots=True)
class CheckReport:
    issues: tuple[CheckIssue, ...]
    checks: tuple[CheckResult, ...]

    def __post_init__(self) -> None:
        _freeze_sequences(self, "issues", "checks")

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def complete(self) -> bool:
        return all(
            check.status not in {"incomplete", "skipped"} for check in self.checks
        )


@dataclass(frozen=True, slots=True)
class CheckContext:
    composition: CompositionInfo
    feature: FeatureInfo


type FeatureCheck = Callable[[CheckContext], tuple[CheckIssue, ...] | list[CheckIssue]]


def _freeze_sequences(instance: object, *names: str) -> None:
    for name in names:
        object.__setattr__(instance, name, tuple(getattr(instance, name)))
