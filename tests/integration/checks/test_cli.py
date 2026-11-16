import json
import runpy
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("contract", "code", "http_status"),
    [
        ("declared", None, 404),
        ("undeclared", "CAN001", 404),
        ("unmapped", "CAN002", 500),
    ],
)
def test_source_contract_gaps_and_runtime_error_mapping_are_distinct(
    tmp_path: Path, contract: str, code: str | None, http_status: int
) -> None:
    application = tmp_path / "application.py"
    missing_path = json.dumps(str(tmp_path / "absent.txt"))
    application.write_text(
        dedent(f"""
from pathlib import Path
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Error, ErrorOptions, Feature

class Missing(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')

def require_document() -> str:
    try:
        return Path({missing_path}).read_text(encoding='utf-8')
    except FileNotFoundError as error:
        raise Missing() from error

router = APIRouter()
@router.get('/document', raises=[{"missing" if contract == "declared" else ""}])
def read_document() -> str:
    return require_document()

feature = Feature(
    name='files', routers=[router],
    errors=[{"missing" if contract == "undeclared" else ""}],
)
app = FastAPI()
Composition(
    features=[feature], errors=ErrorOptions(type_base='urn:test:problem'),
).build(app)
"""),
        encoding="utf-8",
    )
    checked = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "fastapi_canon",
            "check",
            "errors",
            str(tmp_path),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert checked.returncode == int(code is not None), checked.stderr
    report = json.loads(checked.stdout)
    (operation,) = report["operations"]
    assert operation["complete"]
    assert [finding["code"] for finding in operation["diagnostics"]] == (
        [] if code is None else [code]
    )

    app = runpy.run_path(str(application))["app"]
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/document")
    assert response.status_code == http_status
    assert response.json()["code"] == (
        "internal_server_error" if contract == "unmapped" else "missing"
    )
