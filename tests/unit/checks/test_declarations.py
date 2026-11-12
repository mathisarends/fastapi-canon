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
raise RuntimeError('The checker must not import inspected source')
class Missing(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')
feature = Feature(name='api', errors=[missing])
router = APIRouter()
@router.get('/health')
def health(): return None
"""


@pytest.fixture
def scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[[str], tuple[int, dict[str, Any]]]:
    def run(source: str) -> tuple[int, dict[str, Any]]:
        (tmp_path / "api.py").write_text(dedent(PRELUDE + source), encoding="utf8")
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


@pytest.mark.parametrize("declared", [False, True])
def test_child_channels_inherit_contracts_and_streams_follow_yielded_bodies(
    scan: Callable[[str], tuple[int, dict[str, Any]]], declared: bool
) -> None:
    status, report = scan(f"""
parent = RpcChannel('parent', raises={"[missing]" if declared else "[]"})
child = parent.child('nested').child('leaf')
@child.method()
def method(): raise Missing()
@child.stream()
async def stream():
    yield 1
    raise Missing()
""")
    assert len(report["operations"]) == 3
    assert report["incomplete_operations"] == 0
    assert report["contract_gaps"] == (0 if declared else 2)
    assert status == int(not declared)


@pytest.mark.parametrize(
    "registration",
    ["router.socket('/raw')(raw)", "declare = router.socket('/raw')\ndeclare(raw)"],
)
def test_direct_socket_registration_keeps_imported_handler_and_registration_location(
    scan: Callable[[str], tuple[int, dict[str, Any]]], tmp_path: Path, registration: str
) -> None:
    (tmp_path / "handlers.py").write_text("async def raw(): pass\n", encoding="utf8")
    status, report = scan("from handlers import raw\n" + registration)
    assert status == 1
    health, socket = report["operations"]
    assert health["complete"]
    assert socket["function"] == "handlers.raw"
    assert Path(socket["path"]).name == "api.py"
    assert socket["transport"] == "socket" and not socket["complete"]
    assert [d["code"] for d in socket["diagnostics"]] == ["CAN003"]


def test_router_created_channel_keeps_children_visible_when_composition_is_unresolved(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = scan("""
channel = router.channel('/rpc').child('child')
@channel.method()
def method(): return None
@channel.stream()
async def events(): yield 1
""")
    assert status == 1
    assert len(report["operations"]) == 3
    assert report["incomplete_operations"] == 2
    assert all(o["transport"] == "rpc" for o in report["operations"][1:])


def test_unapplied_decorators_and_unrelated_registries_are_not_operations(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = scan("""
channel = RpcChannel('unused')
unused = channel.method('unused')
raw = router.socket('/unused')
class Registry:
    def child(self, name): return self
    def stream(self): return lambda fn: fn
other = Registry().child('other')
@other.stream()
def unrelated(): raise Missing()
""")
    assert status == 0
    assert len(report["operations"]) == 1


def test_imperative_factories_keep_aliases_and_conditional_receiver_ownership(
    scan: Callable[[str], tuple[int, dict[str, Any]]], tmp_path: Path
) -> None:
    (tmp_path / "handlers.py").write_text("async def raw(): pass\n", encoding="utf8")
    status, report = scan("""
from handlers import raw
def create_router(enabled: bool):
    local = APIRouter()
    declare = local.socket('/first')
    declare(raw)
    if enabled:
        alternative = local
    else:
        alternative = object()
    alternative.socket('/second')(raw)
    local.binary_stream('/binary')(raw)
    return local
""")
    assert status == 1
    assert len(report["operations"]) == 4
    assert report["incomplete_operations"] == 3
    assert all(o["function"] == "handlers.raw" for o in report["operations"][1:])


def test_nested_child_decorators_remain_visible_when_factory_cannot_be_evaluated(
    scan: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = scan("""
def create_channel(config):
    local = RpcChannel(config)
    child = local.child('nested')
    @child.method()
    def handler(): raise Missing()
    return local
""")
    assert status == 1
    assert len(report["operations"]) == 2
    assert report["incomplete_operations"] == 1
