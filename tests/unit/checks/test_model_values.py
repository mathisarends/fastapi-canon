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


@pytest.mark.parametrize("validate", [False, True])
def test_type_adapter_checks_model_hooks_when_validation_executes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], validate: bool
) -> None:
    statement = (
        "adapter.validate_python({'payload': 'value'})"
        if validate
        else "TypeAdapter(Body)"
    )
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
class Body(BaseModel):
    payload: str
    @field_validator('payload')
    @classmethod
    def reject(cls,value): raise Missing()
adapter = TypeAdapter(Body)
@router.get('/')
def route(): {statement}
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
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert operation["complete"]
    assert report["contract_gaps"] == int(validate)


def test_annotated_validator_outputs_cannot_claim_native_field_types(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + """
from typing import Annotated
from pydantic import PlainValidator
def replace(value): return Risky()
class Body(BaseModel):
    payload: Annotated[str, PlainValidator(replace)]
@router.get('/')
def route(): Body(payload='value').payload.run()
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
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert not operation["complete"]


def test_adapter_for_an_unmodeled_container_schema_remains_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + """
class Body(BaseModel): payload: str
adapter = TypeAdapter(list[Body])
@router.get('/')
def route(): adapter.validate_python([{'payload':'value'}])
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
    assert not operation["complete"]
    assert status == 1


@pytest.mark.parametrize(
    "creation", ["Body(payload=payload)", "Body.model_validate({'payload': payload})"]
)
@pytest.mark.parametrize("risky", [False, True])
def test_before_scalar_validator_checks_the_unvalidated_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], creation: str, risky: bool
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
class Body(BaseModel):
    payload: str
    @field_validator('payload', mode='before')
    @classmethod
    def convert(cls, value: Any):
        if isinstance(value, Risky): value.run()
        return 'validated'
@router.get('/')
def route():
    payload = {"Risky()" if risky else "'safe'"}
    body = {creation}
    body.payload.strip()
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
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == (
        ["CAN001"] if risky else []
    )
    if risky:
        assert operation["diagnostics"][0]["trace"][-1] == "api.Risky.run"


@pytest.mark.parametrize(
    ("mode", "annotation"), [("plain", "str"), ("wrap", "str"), ("before", "Any")]
)
def test_unsupported_validator_outputs_remain_incomplete_without_hiding_known_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str, annotation: str
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
class Body(BaseModel):
    payload: {annotation}
    @field_validator('payload', mode='{mode}')
    @classmethod
    def convert(cls, value: Any):
        value.run()
        return value
@router.get('/')
def route(): Body(payload=Risky())
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
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert not operation["complete"]
    assert {d["code"] for d in operation["diagnostics"]} == {"CAN001", "CAN003"}


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
