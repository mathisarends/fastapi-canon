"""Immutable composition descriptions and system-check results."""

from collections.abc import Callable
from dataclasses import InitVar, dataclass
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


class _EndpointIndex:
    __slots__ = ("_endpoints",)

    _endpoints: tuple[Callable[..., object], ...]


@dataclass(frozen=True, slots=True)
class CompositionInfo(_EndpointIndex):
    features: tuple[FeatureInfo, ...]
    operations: tuple[OperationInfo, ...]
    endpoints: InitVar[tuple[Callable[..., object], ...]] = ()

    def __post_init__(self, endpoints: tuple[Callable[..., object], ...]) -> None:
        _freeze_sequences(self, "features", "operations")
        if endpoints and len(endpoints) != len(self.operations):
            raise ValueError(
                "Endpoint references must match the operation descriptions"
            )
        object.__setattr__(self, "_endpoints", tuple(endpoints))

    def operation(self, endpoint: Callable[..., object]) -> OperationInfo:
        """Look up a registered callable by identity without invoking it."""
        if not callable(endpoint):
            raise TypeError(
                "operation() requires an endpoint callable; use operation_by_id() for IDs"
            )
        matches = tuple(
            operation
            for candidate, operation in zip(
                self._endpoints, self.operations, strict=False
            )
            if candidate is endpoint
        )
        if len(matches) != 1:
            locations = ", ".join(f"{'|'.join(op.methods)} {op.path}" for op in matches)
            raise LookupError(
                f"Expected one operation for endpoint, found {len(matches)}"
                + (f": {locations}" if locations else "")
            )
        return matches[0]

    def operation_by_id(self, operation_id: str) -> OperationInfo:
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


def qualified_name(value: object) -> str:
    """Return the ``module.qualname`` used to locate a finding's subject."""
    subject = value if hasattr(value, "__qualname__") else type(value)
    return (
        f"{getattr(subject, '__module__', '')}.{getattr(subject, '__qualname__', '')}"
    )


def _freeze_sequences(instance: object, *names: str) -> None:
    for name in names:
        object.__setattr__(instance, name, tuple(getattr(instance, name)))
