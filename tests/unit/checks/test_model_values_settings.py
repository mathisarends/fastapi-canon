import json
from pathlib import Path

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings
from fastapi_canon import APIRouter, Error, Feature
class Missing(Exception): pass
missing = Error(Missing,status=404,code='missing',title='Missing')
catalog = Feature(name='api',errors=[missing])
router = APIRouter()
"""


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
