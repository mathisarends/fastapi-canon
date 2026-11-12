import json
from pathlib import Path

import pytest

from fastapi_canon.cli import main


def test_generic_factory_ports_keep_distinct_argument_and_error_graphs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        """
from dishka import Provider, Scope, provide
from fastapi_canon import APIRouter, Inject, Error, Feature
class Missing(Exception): pass
class Conflict(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')
conflict = Error(Conflict, status=409, code='conflict', title='Conflict')
catalog = Feature(name='api', errors=[missing, conflict])
class First: pass
class Second: pass
class Port[T]: pass
class ReadFirst(Port[First]):
    def run(self): raise Missing()
class ReadSecond(Port[Second]):
    def run(self): raise Conflict()
class Services(Provider):
    scope = Scope.APP
    @provide
    def first(self) -> Port[First]: return ReadFirst()
    @provide
    def second(self) -> Port[Second]: return ReadSecond()
class UseCase:
    def __init__(self, port: Port[Second]): self.port = port
    def run(self): self.port.run()
class Cases(Provider):
    use_case = provide(UseCase, scope=Scope.APP)
router = APIRouter()
@router.get('/first', raises=[missing])
def first(port: Inject[Port[First]]): port.run()
@router.get('/second')
def second(use_case: Inject[UseCase]): use_case.run()
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
    first, second = json.loads(capsys.readouterr().out)["operations"]
    assert first["complete"] and first["diagnostics"] == []
    assert second["complete"]
    assert [d["code"] for d in second["diagnostics"]] == ["CAN001"]
    assert second["diagnostics"][0]["trace"][-1] == "api.ReadSecond.run"
