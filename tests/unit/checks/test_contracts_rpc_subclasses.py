import json
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main

PRELUDE = """
from fastapi_canon import APIRouter, Error
from fastapi_canon.integrations.websockets import RpcChannel

raise RuntimeError('Source must never be imported')

class Missing(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')

router = APIRouter()
@router.get('/health')
def health(): return {'ok': True}
"""


@pytest.fixture
def analyze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[[str], tuple[int, dict[str, Any]]]:
    def run(source: str) -> tuple[int, dict[str, Any]]:
        (tmp_path / "api.py").write_text(
            dedent(PRELUDE) + dedent(source), encoding="utf-8"
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
    ("declaration", "exception", "catalog", "expected"),
    [
        ("", "Missing", "", "CAN002"),
        ("", "Missing", "feature = Feature(name='api', errors=[missing])", "CAN001"),
        ("raises=[missing]", "Missing", "", None),
        ("raises=[missing]", "Other", "", "CAN002"),
    ],
)
def test_rpc_subclass_operations_cannot_hide_behind_a_complete_http_operation(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
    declaration: str,
    exception: str,
    catalog: str,
    expected: str | None,
) -> None:
    status, report = analyze(f'''
from fastapi_canon import Feature
{catalog}
class ProjectChannel(RpcChannel):
    """Application channel name."""
    pass

class ChildChannel(ProjectChannel): pass
Alias = ChildChannel
channel = Alias('projects', {declaration})
class Other(Exception): pass

@channel.method
def find(): raise {exception}()
''')
    health, rpc = report["operations"]
    assert health["function"] == "api.health" and health["complete"]
    assert rpc["function"] == "api.find" and rpc["transport"] == "rpc"
    assert rpc["complete"] and report["incomplete_operations"] == 0
    assert [item["code"] for item in rpc["diagnostics"]] == (
        [expected] if expected else []
    )
    assert status == int(expected is not None)


@pytest.mark.parametrize(
    "classes",
    [
        "class ProjectChannel(RpcChannel):\n    def __init__(self, name): pass",
        "class ProjectChannel(RpcChannel):\n    def method(self, function): return function",
        "class ProjectChannel(RpcChannel):\n    method = custom_method",
        "@decorate\nclass ProjectChannel(RpcChannel): pass",
        "class ProjectChannel(RpcChannel, metaclass=Meta): pass",
        "class ProjectChannel[T](RpcChannel): pass",
        "class ProjectChannel(RpcChannel[int]): pass",
        "class ProjectChannel(Mixin, RpcChannel): pass",
        "class ParentChannel(RpcChannel):\n    def method(self, function): return function\nclass ProjectChannel(ParentChannel): pass",
        "class ParentChannel(ProjectChannel): pass\nclass ProjectChannel(ParentChannel, RpcChannel): pass",
    ],
)
def test_customized_rpc_subclasses_remain_visible_and_incomplete(
    analyze: Callable[[str], tuple[int, dict[str, Any]]], classes: str
) -> None:
    status, report = analyze(f"""
{classes}
channel = ProjectChannel('projects', raises=[missing])
@channel.method(raises=[missing])
def find(): raise Missing()
""")
    health, rpc = report["operations"]
    assert health["complete"]
    assert status == 1 and report["incomplete_operations"] == 1
    assert not rpc["complete"] and rpc["transport"] == "rpc"
    assert [item["code"] for item in rpc["diagnostics"]] == ["CAN003"]
    assert "subclass" in rpc["diagnostics"][0]["message"]
    assert report["contract_gaps"] == 0


def test_imported_rpc_subclass_uses_channel_and_method_contracts(
    analyze: Callable[[str], tuple[int, dict[str, Any]]], tmp_path: Path
) -> None:
    (tmp_path / "channels.py").write_text(
        "from fastapi_canon.integrations.websockets import RpcChannel as Base\n"
        "class ProjectChannel(Base): pass\n",
        encoding="utf-8",
    )
    status, report = analyze("""
from channels import ProjectChannel
channel = ProjectChannel('projects', raises=[missing])
@channel.method
def find(): raise Missing()
""")
    assert status == 0 and report["contract_gaps"] == 0
    assert report["operations"][1]["complete"]


def test_rpc_subclass_method_contract_covers_its_error(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class ProjectChannel(RpcChannel): pass
channel = ProjectChannel('projects')
@channel.method(raises=[missing])
def find(): raise Missing()
""")
    assert status == 0 and report["operations"][1]["diagnostics"] == []


@pytest.mark.parametrize("reexport", [False, True])
def test_uncertain_imported_base_prevents_a_complete_rpc_report(
    analyze: Callable[[str], tuple[int, dict[str, Any]]], tmp_path: Path, reexport: bool
) -> None:
    declaration = (
        "from fastapi_canon.integrations.websockets import RpcChannel\n"
        "class ProjectChannel(RpcChannel): pass\n"
    )
    if reexport:
        (tmp_path / "channel_base.py").write_text(declaration, encoding="utf-8")
        declaration = "from channel_base import ProjectChannel\n"
    (tmp_path / "channels.py").write_text(
        declaration + "if configured: setting = True\n",
        encoding="utf-8",
    )
    status, report = analyze("""
from channels import ProjectChannel
class ChildChannel(ProjectChannel): pass
channel = ChildChannel('projects')
@channel.method
def find(): raise Missing()
""")
    rpc = report["operations"][1]
    assert status == 1 and not rpc["complete"]
    assert report["contract_gaps"] == 0
    assert any(item["code"] == "CAN003" for item in rpc["diagnostics"])


@pytest.mark.parametrize("customized", [False, True])
def test_rpc_subclasses_inside_supported_local_factories(
    analyze: Callable[[str], tuple[int, dict[str, Any]]], customized: bool
) -> None:
    body = "def __init__(self, name, **kwargs): pass" if customized else "pass"
    status, report = analyze(f"""
class ProjectChannel(RpcChannel):
    {body}
def create_channel():
    channel = ProjectChannel('projects', raises=[missing])
    @channel.method
    def find(): raise Missing()
    return channel
""")
    rpc = report["operations"][1]
    assert rpc["function"] == "api.create_channel.<locals>.find"
    assert rpc["transport"] == "rpc" and rpc["complete"] is not customized
    assert status == int(customized)


def test_unrelated_method_decorators_still_do_not_count_as_rpc(
    analyze: Callable[[str], tuple[int, dict[str, Any]]],
) -> None:
    status, report = analyze("""
class Registry:
    def method(self, function): return function
class CustomRegistry(Registry): pass
channel = CustomRegistry()
@channel.method
def find(): raise Missing()
""")
    assert status == 0
    assert [item["function"] for item in report["operations"]] == ["api.health"]
