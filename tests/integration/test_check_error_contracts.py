import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.mark.parametrize(
    ("contract", "code", "http_status"),
    [
        ("declared", None, 404),
        ("undeclared", "CAN001", 404),
        ("unmapped", "CAN002", 500),
    ],
)
def test_forwarded_provider_agrees_with_real_file_error_handling(
    tmp_path: Path, contract: str, code: str | None, http_status: int
) -> None:
    application = tmp_path / "application.py"
    missing_path = json.dumps(str(tmp_path / "absent.txt"))
    application.write_text(
        dedent(f"""
from abc import ABC, abstractmethod
from pathlib import Path
from dishka import Provider, Scope, provide
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Error, ErrorOptions, Feature, Inject

class Missing(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')

class Repository(ABC):
    @abstractmethod
    async def read(self) -> str: ...

class FileRepository(Repository):
    async def read(self) -> str:
        try:
            return Path({missing_path}).read_text(encoding='utf-8')
        except FileNotFoundError as error:
            raise Missing() from error

class AppProvider(Provider):
    files = provide(FileRepository, scope=Scope.REQUEST)
    @provide(scope=Scope.REQUEST)
    def repository(self, files: FileRepository) -> Repository:
        return files

router = APIRouter()
@router.get('/document', operation_id='read_document', raises=[{"missing" if contract == "declared" else ""}])
async def read_document(repository: Inject[Repository]) -> str:
    return await repository.read()

feature = Feature(
    name='files', routers=[router], providers=[AppProvider()],
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
    report = json.loads(checked.stdout)
    (operation,) = report["operations"]
    assert checked.returncode == int(code is not None), checked.stderr
    assert operation["complete"]
    assert report["incomplete_operations"] == 0
    assert [finding["code"] for finding in operation["diagnostics"]] == (
        [] if code is None else [code]
    )

    served = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import json, runpy, sys\n"
            "from fastapi.testclient import TestClient\n"
            "app = runpy.run_path(sys.argv[1])['app']\n"
            "with TestClient(app, raise_server_exceptions=False) as client:\n"
            "    response = client.get('/document')\n"
            "    print(json.dumps({'status': response.status_code, 'body': response.json()}))\n",
            str(application),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert served.returncode == 0, served.stderr
    response = json.loads(served.stdout)
    assert response["status"] == http_status
    assert response["body"]["code"] == (
        "internal_server_error" if contract == "unmapped" else "missing"
    )
