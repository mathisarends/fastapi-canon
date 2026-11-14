import json
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from fastapi_canon.cli import main


@pytest.mark.parametrize(
    ("transport", "declaration", "decorator", "body"),
    [
        ("http", "APIRouter", "get('/')", "raise Missing()"),
        ("sse", "APIRouter", "sse('/')", "yield 1\n    raise Missing()"),
        ("webrtc", "WebRtcRouter", "session('/')", "raise Missing()"),
        ("rpc", "RpcChannel", "method()", "raise Missing()"),
    ],
)
@pytest.mark.parametrize("contract", ["unmapped", "undeclared", "declared"])
def test_reexported_router_keeps_its_error_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    transport: str,
    declaration: str,
    decorator: str,
    body: str,
    contract: str,
) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from .router import router\n", encoding="utf-8"
    )
    catalog = "catalog = Feature(name='app', errors=[missing])"
    arguments = "raises=[missing]" if contract == "declared" else ""
    if transport == "rpc":
        arguments = f"'app', {arguments}"
    (package / "router.py").write_text(
        dedent(f"""
from collections.abc import AsyncIterator
from fastapi_canon import APIRouter, Error, Feature
from fastapi_canon.integrations.webrtc import WebRtcRouter
from fastapi_canon.integrations.websockets import RpcChannel

raise RuntimeError('Application source must not be imported')

class Missing(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')
{catalog if contract == "undeclared" else ""}
router = {declaration}({arguments})

@router.{decorator}
async def endpoint() -> {"AsyncIterator[int]" if transport == "sse" else "None"}:
    {body}
"""),
        encoding="utf-8",
    )

    status = main(["check", "errors", str(tmp_path), "--strict", "--format", "json"])
    report = json.loads(capsys.readouterr().out)

    (operation,) = report["operations"]
    expected = {"unmapped": ["CAN002"], "undeclared": ["CAN001"], "declared": []}
    assert status == int(contract != "declared")
    assert operation["function"] == "app.router.endpoint"
    assert operation["transport"] == transport
    assert operation["complete"]
    assert [item["code"] for item in operation["diagnostics"]] == expected[contract]
    assert report["contract_gaps"] == int(contract != "declared")
    assert report["incomplete_operations"] == 0


@pytest.mark.parametrize(
    ("imports", "call"),
    [
        ("from app import fail", "fail()"),
        ("from app import fail as run", "run()"),
        ("import app as application", "application.fail()"),
        ("from facade import run", "run()"),
    ],
)
def test_reexported_helper_named_after_its_module_is_followed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    imports: str,
    call: str,
) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("from .fail import fail\n", encoding="utf-8")
    (package / "fail.py").write_text(
        "class Missing(Exception): pass\n\ndef fail(): raise Missing()\n",
        encoding="utf-8",
    )
    (tmp_path / "facade.py").write_text(
        "from app import fail as run\n", encoding="utf-8"
    )
    (tmp_path / "api.py").write_text(
        f"from fastapi_canon import APIRouter\n{imports}\n"
        f"router = APIRouter()\n@router.get('/')\ndef endpoint(): {call}\n",
        encoding="utf-8",
    )

    status = main(["check", "errors", str(tmp_path), "--strict", "--format", "json"])
    report: dict[str, Any] = json.loads(capsys.readouterr().out)

    (operation,) = report["operations"]
    (diagnostic,) = operation["diagnostics"]
    assert status == 1
    assert operation["complete"]
    assert diagnostic["code"] == "CAN002"
    assert diagnostic["trace"] == ["api.endpoint", "app.fail.fail"]
    assert "app.fail.Missing" in diagnostic["message"]
