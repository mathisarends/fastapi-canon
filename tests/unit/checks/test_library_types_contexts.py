import json
import sys
from pathlib import Path

import pytest

from fastapi_canon.cli import main


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("owned_argument", [False, True])
def test_external_context_signatures_preserve_values_and_own_protocol_boundaries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    asynchronous: bool,
    owned_argument: bool,
) -> None:
    (tmp_path / "context_fixture.py").write_text(
        f"""
from contextlib import {"asynccontextmanager" if asynchronous else "contextmanager"}
from collections.abc import {"AsyncIterator" if asynchronous else "Iterator"}
raise RuntimeError('external source must not execute')
class Client:
    def ping(self) -> str: ...
@{"asynccontextmanager" if asynchronous else "contextmanager"}
{"async " if asynchronous else ""}def opened[T](value: T) -> {"AsyncIterator" if asynchronous else "Iterator"}[T]:
    yield value
""",
        encoding="utf8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    app = tmp_path / "app"
    app.mkdir()
    (app / "api.py").write_text(
        f"""
from fastapi_canon import APIRouter, Error, Feature
from context_fixture import Client, opened
class Missing(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')
feature = Feature(name='api', errors=[missing])
class Risky:
    def ping(self): raise Missing()
router = APIRouter()
@router.get('/')
{"async " if asynchronous else ""}def endpoint():
    {"async " if asynchronous else ""}with opened({"Risky()" if owned_argument else "Client()"}) as value:
        value.ping()
    raise Missing()
""",
        encoding="utf8",
    )
    main(
        [
            "check",
            "errors",
            str(app),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ]
    )
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert operation["complete"] is not owned_argument
    assert any(d["code"] == "CAN001" for d in operation["diagnostics"])
    assert "context_fixture" not in sys.modules


def test_external_context_value_survives_owned_provider_entry(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "provider_context_fixture.py").write_text(
        """
from contextlib import contextmanager
from collections.abc import Iterator
class Client:
    def ping(self) -> str: ...
@contextmanager
def opened[T](client: T) -> Iterator[T]: yield client
""",
        encoding="utf8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    app = tmp_path / "app"
    app.mkdir()
    (app / "api.py").write_text(
        """
from collections.abc import Iterator
from dishka import Provider, Scope, provide
from fastapi_canon import APIRouter, Error, Feature, Inject
from provider_context_fixture import Client, opened
class Missing(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')
feature = Feature(name='api', errors=[missing])
class Service:
    def run(self): raise Missing()
class Services(Provider):
    scope = Scope.APP
    @provide
    def service(self) -> Iterator[Service]:
        with opened(Client()) as client:
            client.ping()
            yield Service()
router = APIRouter()
@router.get('/')
def endpoint(service: Inject[Service]): service.run()
""",
        encoding="utf8",
    )
    main(
        [
            "check",
            "errors",
            str(app),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ]
    )
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN001"]
    assert "provider_context_fixture" not in sys.modules
