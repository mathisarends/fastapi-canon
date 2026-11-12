import json
from pathlib import Path

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal
from pydantic import BeforeValidator, TypeAdapter
from fastapi_canon import APIRouter, Error, Feature
class Missing(Exception): pass
class Invalid(ValueError): pass
missing = Error(Missing, status=404, code='missing', title='Missing')
feature = Feature(name='api', errors=[missing])
router = APIRouter()
"""


@pytest.mark.parametrize("post_init", ["", "Missing", "Invalid"])
def test_dataclass_adapters_preserve_fields_and_post_init_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], post_init: str
) -> None:
    hook = f"    def __post_init__(self): raise {post_init}()\n" if post_init else ""
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
@dataclass(frozen=True, slots=True)
class Body:
    text: str
{hook}
adapter = TypeAdapter(tuple[Body, ...])
@router.get('/')
def route():
    for body in adapter.validate_python([{{'text':'safe'}}]): body.text.strip()
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
    assert [d["code"] for d in operation["diagnostics"]] == (
        ["CAN001"] if post_init == "Missing" else []
    )


def test_dataclass_enum_literal_retains_application_methods(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + """
class Kind(StrEnum):
    MAIN = 'main'
    def guard(self): raise Missing()
@dataclass(frozen=True, slots=True)
class Body:
    kind: Literal[Kind.MAIN] = Kind.MAIN
    def __post_init__(self): self.kind.guard()
adapter = TypeAdapter(Body)
@router.get('/')
def route(): adapter.validate_python({'kind':'main'})
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
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]
    assert operation["diagnostics"][0]["trace"][-1] == "api.Kind.guard"


@pytest.mark.parametrize("boundary", ["factory", "schema", "validator", "metadata"])
def test_unmodeled_dataclass_construction_and_schema_effects_remain_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], boundary: str
) -> None:
    schema = (
        "    @classmethod\n    def __get_pydantic_core_schema__(cls, source, handler): raise Missing()\n"
        if boundary == "schema"
        else ""
    )
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
class Risky:
    def run(self): raise Missing()
def build(): return Risky()
def convert(value): raise Missing()
@dataclass
class Body:
    {"__pydantic_config__ = {'alias_generator': convert}" if boundary == "metadata" else ""}
    text: {"Annotated[str, BeforeValidator(convert)]" if boundary == "validator" else "str"} {"= field(default_factory=build)" if boundary == "factory" else ""}
{schema}
adapter = TypeAdapter(Body)
@router.get('/')
def route(): adapter.validate_python({{}})
""",
        encoding="utf8",
    )
    main(["check", "errors", str(tmp_path), "--require-complete", "--format", "json"])
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert not operation["complete"]
