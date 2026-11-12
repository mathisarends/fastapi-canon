import json
from pathlib import Path
from textwrap import dedent

import pytest

from fastapi_canon.cli import main


@pytest.mark.parametrize(
    ("arguments", "annotation"),
    [("*values", ""), ("**values", ""), ("*values", ": tuple[Safe, ...]")],
)
def test_opaque_arguments_cannot_be_replaced_by_parameter_annotations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: str,
    annotation: str,
) -> None:
    (tmp_path / "api.py").write_text(
        f"""
from fastapi_canon import APIRouter
router = APIRouter()
class Safe:
    def run(self): pass
def invoke(service: Safe): service.run()
@router.get('/')
def endpoint(values{annotation}): invoke({arguments})
""",
        encoding="utf8",
    )
    main(["check", "errors", str(tmp_path), "--require-complete", "--format", "json"])
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert not operation["complete"]
    assert any(
        "unresolved function arguments" in d["message"]
        for d in operation["diagnostics"]
    )


def test_unused_unresolved_generic_dependency_is_not_considered_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        """
from fastapi_canon import APIRouter, Inject
router = APIRouter()
class Service[T]:
    def __init__(self): raise RuntimeError('constructed')
@router.get('/')
def endpoint(service: Inject[Service[str]]): pass
""",
        encoding="utf8",
    )
    main(["check", "errors", str(tmp_path), "--require-complete", "--format", "json"])
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert not operation["complete"]
    assert any(
        "unresolved Dishka dependency type" in d["message"]
        for d in operation["diagnostics"]
    )


@pytest.mark.parametrize(
    "annotation", ["Any", "object", "type", "Callable", "Callable[[], None]"]
)
def test_opaque_annotations_do_not_establish_external_callables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], annotation: str
) -> None:
    (tmp_path / "api.py").write_text(
        dedent(f"""
from typing import Any, Callable
from fastapi_canon import APIRouter
raise RuntimeError('Source must not be imported')
router = APIRouter()
@router.get('/')
def endpoint(value: {annotation}):
    value()
"""),
        encoding="utf-8",
    )
    status = main(
        [
            "check",
            "errors",
            str(tmp_path),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert any(d["code"] == "CAN003" for d in operation["diagnostics"])


def test_unresolved_constructor_parameter_shadows_a_module_function(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        dedent("""
from collections.abc import Callable
from fastapi_canon import APIRouter
def clock(): pass
class Service:
    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
    def run(self):
        self._clock()
router = APIRouter()
@router.get('/')
def endpoint(service: Service):
    service.run()
"""),
        encoding="utf-8",
    )
    status = main(
        [
            "check",
            "errors",
            str(tmp_path),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert any("self._clock" in d["message"] for d in operation["diagnostics"])


@pytest.mark.parametrize(
    ("expression", "complete", "gap"),
    [
        ("{'action': Safe()} | {'other': Risky()}", True, True),
        ("{'action': Risky()} | {'action': Safe()}", True, False),
        ("{'action': Safe()} | supplied", True, True),
        ("{'action': Safe()} | opaque", False, False),
    ],
)
def test_dictionary_union_preserves_values_and_right_hand_overrides(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    expression: str,
    complete: bool,
    gap: bool,
) -> None:
    (tmp_path / "api.py").write_text(
        dedent(f"""
from typing import Any
from fastapi_canon import APIRouter
class Missing(Exception): pass
class Safe:
    def run(self): pass
class Risky:
    def run(self): raise Missing()
router = APIRouter()
@router.get('/')
def endpoint(supplied: dict[str, Risky], opaque: Any):
    merged = {expression}
    for service in merged.values():
        service.run()
"""),
        encoding="utf8",
    )
    status = main(
        [
            "check",
            "errors",
            str(tmp_path),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert operation["complete"] == complete
    assert bool(report["contract_gaps"]) == gap
    assert status == int(gap or not complete)


@pytest.mark.parametrize(
    ("statement", "gap"),
    [
        ("assert fail()", True),
        ("assert False, fail()", True),
        ("assert True, fail()", False),
        ("assert value is None\n    if value is not None: fail()", True),
    ],
)
def test_assertions_follow_application_calls_without_assuming_runtime_optimization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], statement: str, gap: bool
) -> None:
    (tmp_path / "api.py").write_text(
        f"""
from fastapi_canon import APIRouter
class Missing(Exception): pass
def fail(): raise Missing()
router = APIRouter()
@router.get('/')
def endpoint(value: str | None):
    {statement}
""",
        encoding="utf8",
    )
    status = main(
        [
            "check",
            "errors",
            str(tmp_path),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert operation["complete"]
    assert bool(report["contract_gaps"]) == gap
    assert status == int(gap)


@pytest.mark.parametrize("opaque", [False, True])
def test_raised_exception_class_choices_preserve_every_domain_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], opaque: bool
) -> None:
    other = "unknown" if opaque else "Conflict"
    (tmp_path / "api.py").write_text(
        f"""
from typing import Any
from fastapi_canon import APIRouter
class Missing(Exception): pass
class Conflict(Exception): pass
router = APIRouter()
@router.get('/')
def endpoint(code: str, unknown: Any):
    errors = {{'missing': Missing, 'conflict': {other}}}
    raise errors[code]()
""",
        encoding="utf8",
    )
    status = main(
        [
            "check",
            "errors",
            str(tmp_path),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert operation["complete"] == (not opaque)
    gaps = [d for d in operation["diagnostics"] if d["code"] == "CAN002"]
    assert len(gaps) == (1 if opaque else 2)
    assert any("api.Missing" in d["message"] for d in gaps)
    assert status == 1
