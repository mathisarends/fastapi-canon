import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from collections.abc import AsyncIterator
from fastapi import Depends
from fastapi_canon import APIRouter, Error, Feature, Inject
from fastapi_canon.integrations.websockets import RpcChannel

class Missing(Exception):
    pass

class Unmapped(Exception):
    pass

missing = Error(Missing, status=404, code="missing", title="Missing")
router = APIRouter()
channel = RpcChannel("projects")
feature = Feature(name="projects", routers=[router], errors=[missing])
"""


@pytest.fixture
def analyze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[..., dict[str, Any]]:
    def run(source: str, prelude: str = PRELUDE) -> dict[str, Any]:
        (tmp_path / "api.py").write_text(dedent(prelude + source), encoding="utf-8")
        main(["check", "errors", str(tmp_path), "--format", "json"])
        report: dict[str, Any] = json.loads(capsys.readouterr().out)
        return report

    return run


def findings(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [
        (operation["transport"], item["code"], item["message"].rsplit(" ", 1)[-1])
        for operation in report["operations"]
        for item in operation["diagnostics"]
    ]


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [("", [("sse", "CAN001", "raises")] * 2), ("raises=[missing]", [])],
)
def test_sse_dependency_and_body_errors_need_raises(
    analyze: Callable[..., dict[str, Any]],
    declaration: str,
    expected: list[tuple[str, str, str]],
) -> None:
    report = analyze(
        f"""
class Denied(Missing):
    pass

def authenticate() -> str:
    raise Denied()

@router.sse('/events', dependencies=[Depends(authenticate)], {declaration})
async def events() -> AsyncIterator[int]:
    yield 1
    raise Missing()
""",
    )

    assert findings(report) == expected
    assert report["operations"][0]["complete"]


def test_sse_follows_iterated_service_generators(
    analyze: Callable[..., dict[str, Any]],
) -> None:
    report = analyze(
        """
class Service:
    async def items(self) -> AsyncIterator[int]:
        yield 1
        raise Missing()

@router.sse('/events')
async def events(service: Inject[Service]) -> AsyncIterator[int]:
    async for item in service.items():
        yield item
""",
    )

    (operation,) = report["operations"]
    (diagnostic,) = operation["diagnostics"]
    assert diagnostic["code"] == "CAN001"
    assert diagnostic["trace"] == ["api.events", "api.Service.items"]


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [("", [("webrtc", "CAN001", "raises")]), ("raises=[missing]", [])],
)
def test_webrtc_factory_errors_need_raises(
    analyze: Callable[..., dict[str, Any]],
    declaration: str,
    expected: list[tuple[str, str, str]],
) -> None:
    report = analyze(
        f"""
@router.webrtc('/sessions/{{session_id}}', {declaration})
async def session(session_id: str) -> object:
    raise Missing()
""",
    )

    assert findings(report) == expected


@pytest.mark.parametrize(
    ("shared", "decorator", "expected"),
    [
        (False, "@channel.method", [("rpc", "CAN001", "raises")]),
        (False, "@channel.method(raises=[missing])", []),
        (True, "@channel.method", []),
    ],
)
def test_rpc_method_errors_need_raises(
    analyze: Callable[..., dict[str, Any]],
    shared: bool,
    decorator: str,
    expected: list[tuple[str, str, str]],
) -> None:
    prelude = (
        PRELUDE.replace(
            'RpcChannel("projects")',
            'RpcChannel("projects", raises=[missing])',
        )
        if shared
        else PRELUDE
    )
    report = analyze(
        f"""
class Service:
    async def find(self) -> int:
        raise Missing()

{decorator}
async def find(service: Inject[Service]) -> int:
    return await service.find()
""",
        prelude,
    )

    assert findings(report) == expected
    assert report["operations"][0]["complete"]


def test_rpc_unmapped_exception(analyze: Callable[..., dict[str, Any]]) -> None:
    report = analyze(
        """
@channel.method
async def find() -> int:
    raise Unmapped()
""",
    )

    assert [item["code"] for item in report["operations"][0]["diagnostics"]] == [
        "CAN002"
    ]


def test_method_decorators_of_other_objects_are_ignored(
    analyze: Callable[..., dict[str, Any]],
) -> None:
    report = analyze(
        """
class Registry:
    def method(self, function: object) -> object:
        return function

other = Registry()

@other.method
async def find() -> int:
    raise Missing()
""",
    )

    assert report["operations"] == []


def test_loops_propagate_explicit_exceptions(
    analyze: Callable[..., dict[str, Any]],
) -> None:
    report = analyze(
        """
@router.get('/items')
def items(values: list[int]) -> None:
    for value in values:
        if value:
            raise Missing()
    while values:
        values = []
""",
    )

    assert findings(report) == [("http", "CAN001", "raises")]
    assert report["operations"][0]["complete"]
