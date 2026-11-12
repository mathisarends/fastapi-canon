import json
from pathlib import Path

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings
from fastapi_canon import APIRouter, Error, Feature
class Missing(Exception): pass
missing = Error(Missing,status=404,code='missing',title='Missing')
catalog = Feature(name='api',errors=[missing])
router = APIRouter()
"""


@pytest.mark.parametrize(
    ("default", "gaps", "complete"),
    [
        ("Field(False)", 0, True),
        ("Field(True)", 1, True),
        ("Field(**options)", None, False),
    ],
)
def test_settings_field_metadata_retains_only_actual_defaults(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    default: str,
    gaps: int | None,
    complete: bool,
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
options = {{'default_factory': lambda: True}}
class Settings(BaseSettings):
    enabled: bool = {default}
    @field_validator('enabled', mode='before')
    @classmethod
    def guard(cls, value):
        if isinstance(value, bool) and value: raise Missing()
        return value
@router.get('/')
def route(): Settings()
""",
        encoding="utf8",
    )
    main(["check", "errors", str(tmp_path), "--require-complete", "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert operation["complete"] == complete, operation["diagnostics"]
    if gaps is not None:
        assert report["contract_gaps"] == gaps


@pytest.mark.parametrize(
    ("arguments", "complete", "gaps"),
    [
        ("name='fixed'", True, 1),
        ("name='fixed', enabled=False", True, 0),
        ("_env_file='settings.env'", False, None),
    ],
)
def test_settings_explicit_fields_override_only_their_environment_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: str,
    complete: bool,
    gaps: int | None,
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + f"""
class Settings(BaseSettings):
    name: str
    enabled: bool = False
    @model_validator(mode='after')
    def guard(self):
        if self.enabled: raise Missing()
        return self
@router.get('/')
def route(): Settings({arguments})
""",
        encoding="utf8",
    )
    main(["check", "errors", str(tmp_path), "--require-complete", "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert operation["complete"] == complete
    if gaps is not None:
        assert report["contract_gaps"] == gaps


@pytest.mark.parametrize(
    ("model", "complete", "gaps"),
    [
        (
            """
class Settings(BaseSettings):
    name: str
    @field_validator('name',mode='before')
    @classmethod
    def normalize(cls,value): return value.strip()
""",
            True,
            0,
        ),
        (
            """
class Settings(BaseSettings):
    enabled: bool = False
    @model_validator(mode='after')
    def guard(self):
        if self.enabled: raise Missing()
        return self
""",
            True,
            1,
        ),
        (
            """
class Settings(BaseSettings):
    enabled: bool = False
    @classmethod
    def settings_customise_sources(cls,settings_cls,init_settings,env_settings,dotenv_settings,file_secret_settings):
        raise Missing()
""",
            False,
            None,
        ),
    ],
)
def test_settings_sources_are_not_replaced_by_constructor_defaults(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    model: str,
    complete: bool,
    gaps: int | None,
) -> None:
    (tmp_path / "api.py").write_text(
        PRELUDE
        + model
        + """
@router.get('/')
def route(): Settings()
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
    assert operation["complete"] == complete
    if gaps is not None:
        assert report["contract_gaps"] == gaps
