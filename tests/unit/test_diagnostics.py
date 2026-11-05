from dataclasses import FrozenInstanceError
from typing import Literal

import pytest

from fastapi_canon import (
    CheckIssue,
    CheckReport,
    CheckResult,
    CompositionInfo,
    OperationInfo,
)


@pytest.mark.parametrize(
    ("status", "complete"),
    [("passed", True), ("failed", True), ("skipped", False), ("incomplete", False)],
)
def test_report_completeness_is_independent_of_success(
    status: Literal["passed", "failed", "skipped", "incomplete"], complete: bool
) -> None:
    check = CheckResult("configuration", status)
    report = CheckReport(
        (CheckIssue("app.E001", "Missing configuration", "Set the option."),), (check,)
    )
    assert report.complete is complete
    assert report.has_errors


def test_report_warning_does_not_count_as_an_error() -> None:
    report = CheckReport(
        (
            CheckIssue(
                "app.W001",
                "Optional configuration missing",
                "Set the option.",
                severity="warning",
            ),
        ),
        (CheckResult("configuration", "passed"),),
    )
    assert not report.has_errors
    assert report.complete
    with pytest.raises(FrozenInstanceError):
        report.issues = ()  # type: ignore[misc]  # Verify runtime rejection of mutation.


@pytest.mark.parametrize("count", [0, 2])
def test_operation_lookup_rejects_missing_or_ambiguous_ids(count: int) -> None:
    operation = OperationInfo("projects", "projects.get", "/projects", ("GET",))
    snapshot = CompositionInfo((), (operation,) * count)
    with pytest.raises(LookupError, match=f"found {count}"):
        snapshot.operation_by_id("projects.get")


def test_callable_lookup_requires_a_symbol_instead_of_a_string() -> None:
    snapshot = CompositionInfo((), ())
    with pytest.raises(TypeError, match="operation_by_id"):
        snapshot.operation("projects.get")  # type: ignore[arg-type]  # Verify invalid runtime input.
