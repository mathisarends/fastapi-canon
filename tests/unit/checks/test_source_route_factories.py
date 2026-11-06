import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from fastapi_canon import APIRouter, Error, Feature
from fastapi_canon.integrations.websockets import RpcChannel
from fastapi_canon.router import CanonRouter
raise RuntimeError('Source must never be imported')
class Missing(Exception): pass
def fail(): raise Missing()
"""


@pytest.fixture
def analyze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[[str], tuple[int, dict[str, Any]]]:
    def run(body: str) -> tuple[int, dict[str, Any]]:
        (tmp_path / "api.py").write_text(
            dedent(PRELUDE) + dedent(body), encoding="utf-8"
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
        return status, json.loads(capsys.readouterr().out)

    return run


@pytest.mark.parametrize(
    ("mapping", "location"),
    [
        ("none", "global"),
        ("none", "local"),
        ("registered", "global"),
        ("declared", "global"),
        ("declared", "local"),
    ],
)
def test_factory_error_mappings_and_helpers(
    analyze: Callable[[str], tuple[int, dict[str, Any]]], mapping: str, location: str
) -> None:
    declaration = (
        "missing = Error(Missing, status=404, code='missing', title='Missing')"
    )
    global_declaration = declaration if location == "global" else ""
    local_declaration = declaration if location == "local" else "pass"
    # A feature lists application-wide errors that no operation declares.
    listed = (
        "feature = Feature(name='api', errors=[missing])"
        if mapping == "registered"
        else ""
    )
    raises = ", raises=[missing]" if mapping == "declared" else ""
    status, report = analyze(f"""
{global_declaration}
{listed}
def create_router():
    {local_declaration}
    router = APIRouter()
    def helper(): fail()
    @router.get('/'{raises})
    def endpoint(): helper()
    return router
""")
    (operation,) = report["operations"]
    assert operation["complete"] and operation["transport"] == "http"
    assert operation["function"] == "api.create_router.<locals>.endpoint"
    if mapping == "declared":
        assert status == 0 and operation["diagnostics"] == []
    else:
        assert status == 1
        (diagnostic,) = operation["diagnostics"]
        assert diagnostic["code"] == ("CAN001" if mapping == "registered" else "CAN002")
        assert diagnostic["trace"] == [
            "api.create_router.<locals>.endpoint",
            "api.create_router.<locals>.helper",
            "api.fail",
        ]


def test_distinct_factories_and_repeated_calls_keep_contracts_isolated(
    analyze: Callable[[str], tuple[int, dict[str, Any]]], tmp_path: Path
) -> None:
    body = """
def first():
    missing = Error(Missing, status=404, code='missing', title='Missing')
    router = APIRouter()
    @router.get('/', raises=[missing])
    def endpoint(): fail()
    return router
def second():
    router = APIRouter()
    @router.get('/')
    def endpoint(): fail()
    return router
one = first()
two = first()
three = second()
"""
    status, report = analyze(body)
    first, second = report["operations"]
    assert status == 1 and report["incomplete_operations"] == 0
    assert first["function"] == "api.first.<locals>.endpoint"
    assert second["function"] == "api.second.<locals>.endpoint"
    assert first["diagnostics"] == []
    assert [d["code"] for d in second["diagnostics"]] == ["CAN002"]
    lines = (tmp_path / "api.py").read_text().splitlines()
    assert lines[first["line"] - 1].strip() == "@router.get('/', raises=[missing])"
    assert lines[second["line"] - 1].strip() == "@router.get('/')"
    assert Path(first["path"]) == tmp_path / "api.py"


@pytest.mark.parametrize("router_type", ["APIRouter", "CanonRouter"])
def test_uncalled_factory_is_a_source_owned_declaration(
    analyze: Callable[[str], tuple[int, dict[str, Any]]], router_type: str
) -> None:
    status, report = analyze(f"""
def unused_factory():
    router = {router_type}()
    @router.get('/')
    def endpoint(): pass
    return router
""")
    assert status == 0 and len(report["operations"]) == 1
    assert report["operations"][0]["complete"]


@pytest.mark.parametrize(
    "method,body,transport",
    [
        ("get", "return 1", "http"),
        ("sse", "yield 1; fail()", "sse"),
    ],
)
def test_transport_and_deferred_execution_are_preserved(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
    method: str,
    body: str,
    transport: str,
) -> None:
    status, report = analyze(f"""
def factory():
    router = APIRouter()
    @router.{method}('/')
    async def endpoint(): {body}
    return router
""")
    (operation,) = report["operations"]
    assert operation["transport"] == transport and operation["complete"]
    assert status == (1 if method == "sse" else 0)
    assert [d["code"] for d in operation["diagnostics"]] == (
        ["CAN002"] if method == "sse" else []
    )


def test_local_rpc_channel_uses_factory_scope(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
def factory():
    channel = RpcChannel('projects')
    @channel.method
    async def endpoint(): fail()
    return channel
""")
    (operation,) = report["operations"]
    assert status == 1 and operation["complete"] and operation["transport"] == "rpc"
    assert operation["function"] == "api.factory.<locals>.endpoint"
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN002"]


def test_multiple_returned_routers_share_the_application_catalog(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
missing = Error(Missing, status=404, code='missing', title='Missing')
def factory():
    first = APIRouter()
    second = APIRouter()
    @first.get('/', raises=[missing])
    def declared(): fail()
    @second.get('/')
    def undeclared(): fail()
    return first, second
""")
    declared, undeclared = report["operations"]
    assert status == 1 and report["incomplete_operations"] == 0
    assert declared["diagnostics"] == []
    assert [d["code"] for d in undeclared["diagnostics"]] == ["CAN001"]


@pytest.mark.parametrize(
    "factory",
    [
        "def factory(flag):\n    router = APIRouter()\n    @router.get('/')\n    def endpoint(): fail()\n    return router",
        "def factory():\n    router = APIRouter()\n    if flag: router = APIRouter()\n    @router.get('/')\n    def endpoint(): fail()\n    return router",
        "def factory():\n    router = APIRouter()\n    router = APIRouter()\n    @router.get('/')\n    def endpoint(): fail()\n    return router",
        "def factory():\n    router = choose_router()\n    @router.get('/')\n    def endpoint(): fail()\n    return router",
        "def factory():\n    router = APIRouter()\n    captured = choose_value()\n    @router.get('/')\n    def endpoint(): fail()\n    return router",
        "def factory():\n    router = APIRouter()\n    captured = 1 if flag else 2\n    @router.get('/')\n    def endpoint(): fail()\n    return router",
        "def factory():\n    router = APIRouter()\n    @router.get('/')\n    def endpoint(): fail()\n    return choose_router()",
        "@decorate\ndef factory():\n    router = APIRouter()\n    @router.get('/')\n    def endpoint(): fail()\n    return router",
    ],
)
def test_dynamic_factories_remain_incomplete(
    analyze: Callable[[str], tuple[int, dict[str, Any]]], factory: str
) -> None:
    status, report = analyze(factory)
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert [d["code"] for d in operation["diagnostics"]] == ["CAN003"]
    assert "nested route" in operation["diagnostics"][0]["message"]


def test_arbitrary_endpoint_decorator_does_not_become_complete(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
def factory():
    router = APIRouter()
    @router.get('/')
    @decorate
    def endpoint(): fail()
    return router
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert any(
        d["code"] == "CAN003" and "decorator" in d["message"]
        for d in operation["diagnostics"]
    )


def test_repeated_local_endpoint_names_remain_explicitly_incomplete(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
def factory():
    router = APIRouter()
    @router.get('/one')
    def endpoint(): pass
    @router.get('/two')
    def endpoint(): pass
    return router
""")
    assert status == 1 and report["incomplete_operations"] == 2
    assert len({op["function"] for op in report["operations"]}) == 2


def test_module_uncertainty_still_applies_inside_factory(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
if unknown_flag: flag = True
def factory():
    router = APIRouter()
    @router.get('/')
    def endpoint(): pass
    return router
""")
    (operation,) = report["operations"]
    assert status == 1 and not operation["complete"]
    assert any("conditional module" in d["message"] for d in operation["diagnostics"])


def test_many_factories_keep_stable_independent_reports(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze(
        "\n".join(
            f"""
def factory_{index}():
    router = APIRouter()
    @router.get('/')
    def endpoint(): fail()
    return router
"""
            for index in range(100)
        )
    )
    assert status == 1 and len(report["operations"]) == 100
    assert report["incomplete_operations"] == 0 and report["contract_gaps"] == 100
    assert {op["function"] for op in report["operations"]} == {
        f"api.factory_{index}.<locals>.endpoint" for index in range(100)
    }


@pytest.mark.parametrize(
    "capture", ["unknown_name", "unknown_object.value", "lambda: fail()"]
)
def test_unresolved_captures_remain_incomplete(
    analyze: Callable[[str], tuple[int, dict[str, Any]]], capture: str
) -> None:
    status, report = analyze(f"""
def factory():
    captured = {capture}
    router = APIRouter()
    @router.get('/')
    def endpoint(): return captured
    return router
""")
    assert status == 1 and report["incomplete_operations"] == 1


def test_literal_aliases_and_contract_sequences_are_resolved(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
PREFIX = '/projects'
missing = Error(Missing, status=404, code='missing', title='Missing')
ERRORS = (missing,)
def factory():
    Router = APIRouter
    prefix = PREFIX
    errors = [*ERRORS] + []
    router = Router(prefix=prefix, raises=errors)
    @router.get('/')
    def endpoint(): fail()
    return router
""")
    assert status == 0 and report["operations"][0]["complete"]
