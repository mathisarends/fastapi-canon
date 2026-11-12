import json
from pathlib import Path

import pytest

from fastapi_canon.cli import main


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("expression", ["work()", "resource", "make()"])
def test_context_values_preserve_callable_receivers_and_yielded_services(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    asynchronous: bool,
    expression: str,
) -> None:
    prefix = "async " if asynchronous else ""
    decorator = "asynccontextmanager" if asynchronous else "contextmanager"
    (tmp_path / "api.py").write_text(
        f"""
from contextlib import {decorator}
from fastapi_canon import APIRouter
raise RuntimeError('inspected source must not execute')
class Missing(Exception): pass
class Service:
    def run(self): raise Missing()
class Work[T]:
    def __init__(self, service: T): self.service = service
    @{decorator}
    {prefix}def __call__(self): yield self.service
def make(): return Work(Service())()
router = APIRouter()
@router.get('/')
{prefix}def endpoint():
    work = Work(Service())
    resource = work()
    {prefix}with {expression} as service:
        service.run()
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
    [operation] = json.loads(capsys.readouterr().out)["operations"]
    assert status == 1 and operation["complete"], operation["diagnostics"]
    assert [d["message"] for d in operation["diagnostics"]] == [
        "api.Missing: no Error mapping in raises or the application catalog"
    ]
    assert operation["diagnostics"][0]["trace"][-1] == "api.Service.run"


def test_stored_context_keeps_cleanup_errors_and_body_suppression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "api.py").write_text(
        """
from contextlib import contextmanager
from fastapi_canon import APIRouter
class BodyFailure(Exception): pass
class CleanupFailure(Exception): pass
@contextmanager
def opened():
    try:
        yield None
    except BodyFailure:
        raise CleanupFailure()
router = APIRouter()
@router.get('/')
def endpoint():
    resource = opened()
    with resource:
        raise BodyFailure()
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
    assert [d["message"] for d in operation["diagnostics"]] == [
        "api.CleanupFailure: no Error mapping in raises or the application catalog"
    ]


@pytest.mark.parametrize("boundary", ["nested", "opaque", "wrong_protocol"])
def test_context_value_boundaries_preserve_nested_values_and_uncertainty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], boundary: str
) -> None:
    statement = {
        "nested": "async with work() as service: service.run()",
        "opaque": "async with opened(**options) as service: service.run()",
        "wrong_protocol": "with opened(Service()) as service: service.run()",
    }[boundary]
    (tmp_path / "api.py").write_text(
        f"""
from contextlib import asynccontextmanager
from fastapi_canon import APIRouter
class Missing(Exception): pass
class Service:
    def run(self): raise Missing()
@asynccontextmanager
async def opened(service): yield service
class Work:
    @asynccontextmanager
    async def __call__(self):
        async with opened(Service()) as service:
            yield service
router = APIRouter()
@router.get('/')
async def endpoint(options: dict[str, Service]):
    work = Work()
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
    assert operation["complete"] is (boundary == "nested"), operation["diagnostics"]
    assert any(
        d["code"] == ("CAN002" if boundary == "nested" else "CAN003")
        for d in operation["diagnostics"]
    )
