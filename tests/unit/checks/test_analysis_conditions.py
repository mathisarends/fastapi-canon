import json
from pathlib import Path

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from fastapi_canon import APIRouter
class Missing(Exception): pass
def fail(): raise Missing()
router = APIRouter()
"""


@pytest.mark.parametrize(
    "expression", ["False and fail()", "True or fail()", "fail() if False else 'safe'"]
)
def test_unexecuted_expression_branches_do_not_report_own_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], expression: str
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE + f"\n@router.get('/')\ndef route(): return {expression}\n",
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert status == 0 and operation["complete"] and operation["diagnostics"] == []


@pytest.mark.parametrize(
    "expression",
    [
        "items is not None and any(item.strip() for item in items)",
        "items is None or any(item.strip() for item in items)",
        "[item.strip() for item in items] if items else []",
    ],
)
def test_expression_guards_narrow_values_before_consumption(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], expression: str
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"\n@router.get('/')\ndef route(items: tuple[str, ...] | None): return {expression}\n",
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert status == 0 and operation["complete"], operation["diagnostics"]


@pytest.mark.parametrize(
    ("method", "expression"),
    [
        ("__bool__", "value and 'safe'"),
        ("__len__", "'safe' if value else None"),
    ],
)
def test_branch_evaluation_keeps_application_truthiness_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], method: str, expression: str
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
class Value:
    def {method}(self): raise Missing()
@router.get('/')
def route():
    value = Value()
    return {expression}
""",
        encoding="utf8",
    )
    main(
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert operation["complete"], operation["diagnostics"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN002"]
    assert operation["diagnostics"][0]["trace"][-1] == "api.Value." + method


@pytest.mark.parametrize("flag", [False, True])
def test_short_circuit_values_preserve_conditional_assignment_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: bool
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
def safe(): pass
@router.get('/')
def route():
    callback = safe
    {flag} and (callback := fail)
    callback()
""",
        encoding="utf8",
    )
    main(
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert operation["complete"], operation["diagnostics"]
    assert [d["code"] for d in operation["diagnostics"]] == (["CAN002"] if flag else [])


@pytest.mark.parametrize(
    "statement",
    [
        "if value: pass",
        "assert value",
        "while value: break",
        "[1 for item in [0] if value]",
    ],
)
def test_statement_conditions_keep_own_truthiness_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], statement: str
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
class Value:
    def __bool__(self): raise Missing()
@router.get('/')
def route():
    value = Value()
    {statement}
""",
        encoding="utf8",
    )
    main(
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert operation["complete"], operation["diagnostics"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN002"]


def test_nominal_exception_guard_unwraps_a_caught_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + """
class Wrapped(Exception):
    error: Missing
    def __init__(self, error: Missing): self.error = error
@router.get('/')
def route():
    try:
        raise Wrapped(Missing())
    except Wrapped as failure:
        error = failure.error if isinstance(failure, Wrapped) else failure
        raise error
""",
        encoding="utf8",
    )
    main(
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert operation["complete"], operation["diagnostics"]
    assert [d["message"] for d in operation["diagnostics"]] == [
        "api.Missing: no Error mapping in raises or the application catalog"
    ]


@pytest.mark.parametrize(
    "classes",
    [
        """
class MarkerMeta(type):
    def __instancecheck__(cls, instance): return True
class Marker(Exception, metaclass=MarkerMeta): pass
class Other(Exception): pass
""",
        """
class Marker(Exception): pass
class Other(Exception):
    @property
    def __class__(self): return Marker
""",
    ],
)
def test_custom_instance_checks_do_not_establish_a_nominal_guard(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], classes: str
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + classes
        + """
@router.get('/')
def route(value: Other):
    return fail() if isinstance(value, Marker) else None
""",
        encoding="utf8",
    )
    main(
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert any(
        d["code"] == "CAN002" and "api.Missing:" in d["message"]
        for d in operation["diagnostics"]
    )
