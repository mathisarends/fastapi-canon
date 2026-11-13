import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from fastapi_canon import APIRouter, Error, Feature

class Missing(Exception):
    pass

missing = Error(Missing, status=404, code="missing", title="Missing")
feature = Feature(name="api", errors=[missing])
router = APIRouter()
"""

AUTH = """
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import Depends, Header, WebSocket, WebSocketException
from starlette.requests import HTTPConnection
from fastapi_canon import RouterPolicy

class Guard:
    async def __call__(self, token: str) -> None:
        if token != "example":
            raise Missing

@inject
async def verify(
    guard: FromDishka[Guard], connection: HTTPConnection,
    token: str = Header(...),
) -> None:
    try:
        await guard(token)
    except Missing:
        if isinstance(connection, WebSocket):
            raise WebSocketException(code=1008) from None
        raise
"""

FACTORY = """
from typing import Self

class User:
    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def restore(cls, name: str) -> Self:
        return cls(name)

    def validate(self) -> None:
        raise Missing()
"""


@pytest.fixture
def scan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> Callable[[str], tuple[int, dict[str, Any]]]:
    def run(source: str) -> tuple[int, dict[str, Any]]:
        (tmp_path / "api.py").write_text(dedent(source), encoding="utf-8")
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
        return status, json.loads(capsys.readouterr().out)

    return run


@pytest.mark.parametrize(
    ("contract", "expected"),
    [
        ("declared", []),
        ("catalog", ["CAN001"]),
        ("unmapped", ["CAN002"]),
    ],
)
@pytest.mark.parametrize("dependency", ["policy", "parameter"])
def test_shared_auth_preserves_reraise_and_http_context(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
    contract: str,
    expected: list[str],
    dependency: str,
) -> None:
    prelude = (
        PRELUDE
        if contract != "unmapped"
        else PRELUDE.replace(
            'feature = Feature(name="api", errors=[missing])',
            "",
        )
    )
    raises = "raises=[missing]," if contract == "declared" else ""
    declaration = (
        f"policy = RouterPolicy(dependencies=[Depends(verify)], {raises})\n"
        "router = APIRouter(policy=policy)\n"
        '@router.get("/protected")\nasync def protected() -> None: pass\n'
        if dependency == "policy"
        else f'@router.get("/protected", {raises})\n'
        "async def protected(verified: None = Depends(verify)) -> None: pass\n"
    )
    if dependency == "policy":
        prelude = prelude.replace("router = APIRouter()", "")

    status, report = scan(prelude + AUTH + declaration)

    [operation] = report["operations"]
    assert status == bool(expected)
    assert operation["complete"]
    assert [item["code"] for item in operation["diagnostics"]] == expected
    if expected:
        [diagnostic] = operation["diagnostics"]
        assert diagnostic["trace"] == ["api.verify", "api.Guard.__call__"]


@pytest.mark.parametrize(("catalog", "code"), [(True, "CAN001"), (False, "CAN002")])
@pytest.mark.parametrize(
    "invocation",
    [
        'await guard("bad")',
        'guard = Guard(); await guard("bad")',
        'callback = guard; await callback("bad")',
    ],
)
def test_callable_instances_do_not_hide_contract_gaps(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
    catalog: bool,
    code: str,
    invocation: str,
) -> None:
    prelude = (
        PRELUDE
        if catalog
        else PRELUDE.replace(
            'feature = Feature(name="api", errors=[missing])',
            "",
        )
    )

    status, report = scan(
        prelude
        + AUTH
        + f"""
@router.get("/protected")
async def protected(guard: FromDishka[Guard]) -> None:
    {invocation}
"""
    )

    [operation] = report["operations"]
    assert status == 1
    assert operation["complete"]
    assert [item["code"] for item in operation["diagnostics"]] == [code]
    assert operation["diagnostics"][0]["trace"] == [
        "api.protected",
        "api.Guard.__call__",
    ]


@pytest.mark.parametrize("first", ["direct", "dependency"])
def test_only_framework_injected_connections_are_narrowed(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
    first: str,
) -> None:
    direct = """
@router.get("/direct", raises=[missing])
async def direct(guard: FromDishka[Guard], connection: HTTPConnection) -> None:
    await verify(guard, connection, "bad")
"""
    dependency = """
@router.get("/dependency", raises=[missing], dependencies=[Depends(verify)])
async def dependency() -> None: pass
"""

    status, report = scan(
        PRELUDE
        + AUTH
        + (direct + dependency if first == "direct" else dependency + direct)
    )

    assert status == 1
    operations = {item["function"]: item for item in report["operations"]}
    assert operations["api.dependency"]["complete"]
    [diagnostic] = operations["api.direct"]["diagnostics"]
    assert diagnostic["code"] == "CAN003"
    assert "transport narrowing is unresolved" in diagnostic["message"]


@pytest.mark.parametrize(
    "invocation",
    [
        'user = User.restore("example"); user.validate()',
        'User.restore("example").validate()',
        'restore = User.restore; user = restore("example"); user.validate()',
    ],
)
def test_classmethod_self_return_keeps_application_methods(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
    invocation: str,
) -> None:
    status, report = scan(
        PRELUDE
        + FACTORY
        + f"""
@router.get("/user")
async def endpoint() -> None:
    {invocation}
"""
    )

    [operation] = report["operations"]
    assert status == 1
    assert operation["complete"]
    [diagnostic] = operation["diagnostics"]
    assert diagnostic["code"] == "CAN001"
    assert diagnostic["trace"] == ["api.endpoint", "api.User.validate"]


@pytest.mark.parametrize("first", ["base", "child"])
def test_inherited_classmethods_use_the_bound_constructor_and_cache(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
    first: str,
) -> None:
    base = """
@router.get("/base")
def base() -> None: User.restore("example")
"""
    child = """
@router.get("/child")
def child() -> None: Child.restore("example")
"""
    status, report = scan(
        PRELUDE
        + FACTORY
        + """
class Child(User):
    def __init__(self, name: str) -> None:
        raise Missing()
"""
        + (base + child if first == "base" else child + base)
    )

    assert status == 1
    operations = {item["function"]: item for item in report["operations"]}
    assert operations["api.base"]["diagnostics"] == []
    assert operations["api.child"]["complete"]
    [diagnostic] = operations["api.child"]["diagnostics"]
    assert diagnostic["code"] == "CAN001"
    assert diagnostic["trace"] == [
        "api.child",
        "api.User.restore",
        "api.Child.__init__",
    ]


def test_unknown_class_dispatch_stays_incomplete(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = scan(
        PRELUDE
        + FACTORY
        + """
@router.get("/user")
def endpoint(factory: type[User]) -> None:
    factory("example")
"""
    )

    assert status == 1
    assert report["incomplete_operations"] == 1
    assert report["contract_gaps"] == 0
    assert report["operations"][0]["diagnostics"][0]["code"] == "CAN003"


@pytest.mark.parametrize(
    "expression",
    [
        "result = await session.execute(select(1)); result.scalar_one_or_none()",
        "result: Result[tuple[int]] = await session.execute(select(1)); result.scalar_one_or_none()",
        "result = await session.execute(select(1)); alias = result; alias.scalars().all()",
        "result = await session.scalars(select(1)); result.all()",
        "result = await session.stream(select(1)); await result.scalar_one_or_none()",
    ],
)
def test_known_sqlalchemy_results_survive_await_and_assignments(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
    expression: str,
) -> None:
    status, report = scan(
        PRELUDE
        + f"""
from dishka import FromDishka
from sqlalchemy import select
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession

class Application:
    def scalar_one_or_none(self) -> None: raise Missing()

@router.get("/exists")
async def endpoint(session: FromDishka[AsyncSession]) -> None:
    {expression}
"""
    )

    assert status == 0
    assert report["contract_gaps"] == 0
    assert report["incomplete_operations"] == 0
    assert report["operations"][0]["diagnostics"] == []


def test_result_callbacks_are_still_followed(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = scan(
        PRELUDE
        + """
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

class Strategy:
    def __call__(self, item: object) -> int:
        raise Missing()

@router.get("/exists")
async def endpoint(session: AsyncSession, strategy: Strategy) -> None:
    result = await session.execute(select(1))
    result.unique(strategy)
"""
    )

    assert status == 1
    [diagnostic] = report["operations"][0]["diagnostics"]
    assert diagnostic["code"] == "CAN001"
    assert diagnostic["trace"] == ["api.endpoint", "api.Strategy.__call__"]


@pytest.mark.parametrize(
    "expression",
    [
        "result = await session.execute(select(1)); item = result.scalar_one_or_none(); item.validate()",
        "result = await session.unknown(select(1)); result.scalar_one_or_none()",
    ],
)
def test_unknown_external_returns_stay_incomplete(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
    expression: str,
) -> None:
    status, report = scan(
        PRELUDE
        + FACTORY
        + f"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

@router.get("/exists")
async def endpoint(session: AsyncSession) -> None:
    {expression}
"""
    )

    assert status == 1
    assert report["incomplete_operations"] == 1
    assert report["contract_gaps"] == 0
    assert {item["code"] for item in report["operations"][0]["diagnostics"]} == {
        "CAN003"
    }


def test_classmethod_factory_without_errors_is_complete(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = scan(
        PRELUDE
        + FACTORY
        + """
@router.get("/user")
def endpoint() -> str:
    user = User.restore("example")
    return user.name
"""
    )

    assert status == 0
    assert report["operations"][0]["diagnostics"] == []
    assert report["incomplete_operations"] == 0


@pytest.mark.parametrize("invocation", ['guard("bad")', 'Guard()("bad")'])
def test_module_callable_instances_and_constructed_callbacks_are_followed(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
    invocation: str,
) -> None:
    status, report = scan(
        PRELUDE
        + f"""
class Guard:
    def __call__(self, token: str) -> None: raise Missing()

guard = Guard()

@router.get("/protected")
def endpoint() -> None:
    {invocation}
"""
    )

    assert status == 1
    assert report["incomplete_operations"] == 0
    [diagnostic] = report["operations"][0]["diagnostics"]
    assert diagnostic["code"] == "CAN001"
    assert diagnostic["trace"] == ["api.endpoint", "api.Guard.__call__"]


def test_chained_factory_construction_errors_are_followed(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    source = FACTORY.replace("self.name = name", "raise Missing()")
    status, report = scan(
        PRELUDE
        + source
        + """
@router.get("/user")
def endpoint() -> None:
    User.restore("example").validate()
"""
    )

    assert status == 1
    assert report["incomplete_operations"] == 0
    [diagnostic] = report["operations"][0]["diagnostics"]
    assert diagnostic["code"] == "CAN001"
    assert diagnostic["trace"] == [
        "api.endpoint",
        "api.User.restore",
        "api.User.__init__",
    ]


def test_classmethod_callbacks_keep_the_bound_subclass(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = scan(
        PRELUDE
        + FACTORY
        + """
class Child(User):
    def __init__(self, name: str) -> None: raise Missing()

@router.get("/user")
def endpoint() -> None:
    list(map(Child.restore, ["example"]))
"""
    )

    assert status == 1
    assert report["incomplete_operations"] == 0
    [diagnostic] = report["operations"][0]["diagnostics"]
    assert diagnostic["code"] == "CAN001"
    assert diagnostic["trace"] == [
        "api.endpoint",
        "api.User.restore",
        "api.Child.__init__",
    ]


def test_framework_exception_aliases_preserve_translation(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = scan(
        PRELUDE
        + """
from fastapi import WebSocketException
from starlette.exceptions import WebSocketException as TransportError

@router.get("/protected")
def endpoint() -> None:
    try:
        raise WebSocketException(code=1008)
    except TransportError:
        raise Missing() from None
"""
    )

    assert status == 1
    assert report["incomplete_operations"] == 0
    assert [item["code"] for item in report["operations"][0]["diagnostics"]] == [
        "CAN001"
    ]


@pytest.mark.parametrize(
    "exception", ["fastapi.HTTPException", "starlette.exceptions.HTTPException"]
)
def test_framework_http_exceptions_need_no_application_mapping(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
    exception: str,
) -> None:
    status, report = scan(
        PRELUDE
        + f"""
import fastapi
import starlette.exceptions

@router.get("/protected")
def endpoint() -> None:
    raise {exception}(status_code=403)
"""
    )

    assert status == 0
    assert report["operations"][0]["diagnostics"] == []
