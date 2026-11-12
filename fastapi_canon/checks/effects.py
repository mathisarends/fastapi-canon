"""Exception effects and reusable summaries for source analysis."""

from dataclasses import dataclass, field

from fastapi_canon.checks.value_flow import _Value

type _SummaryKey = tuple[
    str, bool, str | None, str | None, tuple[tuple[str, _Value], ...] | None, str
]


@dataclass(frozen=True)
class _Escape:
    exception: str
    trace: tuple[str, ...]
    line: int
    implicit: bool = False


@dataclass(frozen=True)
class _Unknown:
    message: str
    function: str
    line: int
    deferred: str = ""


@dataclass
class _Flow:
    escapes: list[_Escape] = field(default_factory=list)
    unknowns: list[_Unknown] = field(default_factory=list)
    scheduled: set[str] = field(default_factory=set)

    @property
    def signature(
        self,
    ) -> tuple[frozenset[tuple[str, bool]], frozenset[_Unknown], frozenset[str]]:
        return (
            frozenset((escape.exception, escape.implicit) for escape in self.escapes),
            frozenset(self.unknowns),
            frozenset(self.scheduled),
        )

    def extend(self, other: "_Flow") -> None:
        self.escapes.extend(other.escapes)
        self.unknowns.extend(other.unknowns)
        self.scheduled.update(other.scheduled)

    def copy(self) -> "_Flow":
        return _Flow(list(self.escapes), list(self.unknowns), set(self.scheduled))


@dataclass(frozen=True)
class _Summary:
    flow: _Flow
    modules: frozenset[str]
    reads: tuple[tuple[str, _Value], ...]
