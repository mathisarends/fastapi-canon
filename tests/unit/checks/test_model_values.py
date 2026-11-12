import json
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from typing import Any
from pydantic import BaseModel, JsonValue, TypeAdapter, field_validator
from fastapi_canon import APIRouter, Error, Feature
class Missing(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')
feature = Feature(name='api', errors=[missing])
router = APIRouter()
class Risky:
    def run(self): raise Missing()
"""


@pytest.mark.parametrize(
    "creation",
    [
        "Body.model_validate({'payload': Risky()})",
        "TypeAdapter(Body).validate_python({'payload': Risky()})",
        "Body(payload='safe').model_copy(update={'payload': Risky()})",
    ],
)
def test_validated_any_and_unvalidated_copy_keep_application_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], creation: str
) -> None:
    (tmp_path / "api.py").write_text(
        dedent(
            PRELUDE
            + f"""
class Body(BaseModel): payload: Any
@router.get('/')
def route():
    value = {creation}
    value.payload.run()
"""
        ),
        encoding="utf8",
    )
    main(["check", "errors", str(tmp_path), "--format", "json"])
    report: dict[str, Any] = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]

    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


def test_after_validator_return_value_is_used(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        dedent(
            PRELUDE
            + """
class Body(BaseModel):
    payload: str
    @field_validator('payload')
    @classmethod
    def convert(cls, value): return Risky()
@router.get('/')
def route():
    value = Body.model_validate({'payload': 'safe'})
    value.payload.run()
"""
        ),
        encoding="utf8",
    )
    main(["check", "errors", str(tmp_path), "--format", "json"])
    report: dict[str, Any] = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]

    assert any(d["code"] == "CAN001" for d in operation["diagnostics"])


def test_json_value_mutation_is_visible_to_recursive_consumers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        dedent(
            PRELUDE
            + """
class Body(BaseModel): payload: JsonValue
def visit(value):
    if isinstance(value, dict):
        for item in value.values(): visit(item)
    elif isinstance(value, list):
        for item in value: visit(item)
    elif hasattr(value, 'run'):
        value.run()
@router.get('/')
def route(body: Body):
    if isinstance(body.payload, dict):
        body.payload['unsafe'] = Risky()
    visit(body.payload)
"""
        ),
        encoding="utf8",
    )
    main(["check", "errors", str(tmp_path), "--format", "json"])
    report: dict[str, Any] = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]

    assert any(d["code"] == "CAN001" for d in operation["diagnostics"])
