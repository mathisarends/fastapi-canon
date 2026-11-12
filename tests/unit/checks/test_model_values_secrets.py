import json
from pathlib import Path

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from pydantic import BaseModel, Field, SecretStr, SecretBytes, model_validator
from pydantic_settings import BaseSettings
from fastapi_canon import APIRouter, Error, Feature
class Missing(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')
feature = Feature(name='api', errors=[missing])
router = APIRouter()
class Risky:
    def run(self): raise Missing()
"""


@pytest.mark.parametrize(
    ("wrapper", "value"), [("SecretStr", "'safe'"), ("SecretBytes", "b'safe'")]
)
def test_validated_settings_secret_payload_has_native_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], wrapper: str, value: str
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
class Settings(BaseSettings):
    token: {wrapper} = {"Field(min_length=2)" if wrapper == "SecretStr" else "Field(..., min_length=2)"}
    @model_validator(mode='after')
    def validate_token(self):
        self.token.get_secret_value().strip()
        return self
@router.get('/')
def route():
    Settings()
    {wrapper}({value}).get_secret_value().strip()
""",
        encoding="utf8",
    )
    main(["check", "errors", str(tmp_path), "--require-complete", "--format", "json"])
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert operation["complete"], operation["diagnostics"]
    assert operation["diagnostics"] == []


@pytest.mark.parametrize(
    "statement",
    [
        "SecretStr(Risky()).get_secret_value().run()",
        "Body(token=SecretStr(Risky())).token.get_secret_value().run()",
        "secret = SecretStr('safe')\n    secret._secret_value = Risky()\n    secret.get_secret_value().run()",
    ],
)
def test_secret_wrappers_cannot_hide_actual_application_payloads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], statement: str
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
class Body(BaseModel): token: SecretStr
@router.get('/')
def route():
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
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]


def test_unknown_secret_constructor_payload_remains_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + """
@router.get('/')
def route(value): SecretStr(value).get_secret_value().strip()
""",
        encoding="utf8",
    )
    main(["check", "errors", str(tmp_path), "--require-complete", "--format", "json"])
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert not operation["complete"]


def test_model_validation_preserves_secret_subclass_dispatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + """
class UnsafeSecret(SecretStr):
    def get_secret_value(self): raise Missing()
class Body(BaseModel): token: SecretStr
@router.get('/')
def route(): Body(token=UnsafeSecret('safe')).token.get_secret_value()
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
    assert (
        operation["diagnostics"][0]["trace"][-1] == "api.UnsafeSecret.get_secret_value"
    )


def test_abstract_secret_parameters_follow_their_declared_public_payload_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + """
@router.get('/')
def route(secret: SecretStr): secret.get_secret_value().strip()
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
    assert operation["diagnostics"] == []


@pytest.mark.parametrize("wrapped", [False, True])
def test_secret_validation_postconditions_preserve_existing_opaque_instances(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], wrapped: bool
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
class Body(BaseModel): token: SecretStr
@router.get('/')
def route(value):
    Body(token={"SecretStr(value)" if wrapped else "value"}).token.get_secret_value().strip()
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
    assert operation["complete"] is not wrapped, operation["diagnostics"]
    if not wrapped:
        assert operation["diagnostics"] == []
