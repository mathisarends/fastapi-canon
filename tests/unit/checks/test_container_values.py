import json
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from fastapi_canon import APIRouter
class Missing(Exception): pass
def risky(): raise Missing()
router = APIRouter()
"""


def scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str
) -> dict[str, Any]:
    (tmp_path / "api.py").write_text(PRELUDE + dedent(source), encoding="utf8")
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
    result: dict[str, Any] = json.loads(capsys.readouterr().out)
    return result


@pytest.mark.parametrize(
    ("initial", "mutation", "iteration"),
    [
        ("[]", "callbacks.append(risky)", "callbacks"),
        ("[]", "callbacks.extend([risky])", "callbacks"),
        ("set()", "callbacks.add(risky)", "callbacks"),
        ("set()", "callbacks.update([risky])", "callbacks"),
        ("{}", "callbacks.setdefault('group', set()).add(risky)", "callbacks['group']"),
        ("{}", "callbacks.update({'key': risky})", "callbacks.values()"),
        ("{}", "callbacks.update(key=risky)", "callbacks.values()"),
        ("{}", "callbacks.update({}, key=risky)", "callbacks.values()"),
        ("{}", "callbacks.update(**{'key': risky})", "callbacks.values()"),
    ],
)
def test_native_mutations_retain_callables_without_executing_them(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    initial: str,
    mutation: str,
    iteration: str,
) -> None:
    report = scan(
        tmp_path,
        capsys,
        f"""
@router.get('/store')
def store():
    callbacks = {initial}
    {mutation}
@router.get('/invoke')
def invoke():
    callbacks = {initial}
    {mutation}
    for callback in {iteration}: callback()
""",
    )
    store, invoke = report["operations"]
    assert store["complete"] and store["diagnostics"] == []
    assert invoke["complete"]
    assert [d["code"] for d in invoke["diagnostics"]] == ["CAN002"]


def test_later_route_mutations_invalidate_earlier_route_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = scan(
        tmp_path,
        capsys,
        """
callbacks = []
@router.get('/read')
def read():
    for callback in callbacks: callback()
@router.post('/register')
def register(): callbacks.append(risky)
""",
    )
    reader = next(o for o in report["operations"] if o["function"] == "api.read")
    assert reader["complete"]
    assert [d["code"] for d in reader["diagnostics"]] == ["CAN002"]


def test_opaque_extension_keeps_iteration_effects_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = scan(
        tmp_path,
        capsys,
        """
@router.get('/extend')
def extend(unknown):
    callbacks = []
    callbacks.extend(unknown)
""",
    )
    [operation] = report["operations"]
    assert not operation["complete"]
    assert report["contract_gaps"] == 0


def test_custom_hashing_cannot_be_assumed_to_have_native_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = scan(
        tmp_path,
        capsys,
        """
class Item:
    def __hash__(self): raise Missing()
@router.get('/hash')
def hashed():
    items = set()
    items.add(Item())
""",
    )
    [operation] = report["operations"]
    assert not operation["complete"]
    assert any(
        d["message"] == "unresolved native container mutation"
        for d in operation["diagnostics"]
    )
