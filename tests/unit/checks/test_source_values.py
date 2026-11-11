import json
from pathlib import Path
from textwrap import dedent

import pytest

from fastapi_canon.cli import main


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
