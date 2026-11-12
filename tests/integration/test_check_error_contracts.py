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
import asyncio
from collections.abc import AsyncIterator
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
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
    async def read(self) -> str:
        async with self._lock:
            return await self._read()
    async def _read(self) -> str:
        try:
            return Path({missing_path}).read_text(encoding='utf-8')
        except FileNotFoundError as error:
            raise Missing() from error

class AppProvider(Provider):
    files = provide(FileRepository, scope=Scope.REQUEST)
    @provide(scope=Scope.REQUEST)
    async def repository(self, files: FileRepository) -> AsyncIterator[Repository]:
        try:
            yield files
        finally:
            await asyncio.sleep(0)

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


def test_stored_cleanup_failure_is_reported_before_http_500(
    tmp_path: Path,
) -> None:
    application = tmp_path / "application.py"
    application.write_text(
        dedent("""
from contextlib import ExitStack
from dishka import Provider, Scope, provide
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, ErrorOptions, Feature, Inject

class CleanupFailure(Exception): pass
def cleanup():
    raise CleanupFailure('resource cleanup failed')

class Resource:
    stack: ExitStack
    def __init__(self):
        self.stack: ExitStack = ExitStack()
        self.stack.callback(cleanup)
    def close(self):
        self.stack.close()

class Resources(Provider):
    resource = provide(Resource, scope=Scope.REQUEST)

router = APIRouter()
@router.get('/close')
async def close(resource: Inject[Resource]):
    resource.close()

feature = Feature(name='resources', routers=[router], providers=[Resources()])
app = FastAPI()
Composition(features=[feature], errors=ErrorOptions(type_base='urn:test:problem')).build(app)
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
    assert checked.returncode == 1, checked.stderr
    assert operation["complete"]
    assert report["contract_gaps"] == 1
    assert any(
        d["code"] == "CAN002" and "CleanupFailure" in d["message"]
        for d in operation["diagnostics"]
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
            "    response = client.get('/close')\n"
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
    assert response["status"] == 500
    assert response["body"]["code"] == "internal_server_error"


def test_owned_provider_cleanup_is_not_certified_as_a_direct_error_contract(
    tmp_path: Path,
) -> None:
    application = tmp_path / "application.py"
    application.write_text(
        dedent("""
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dishka import AsyncContainer, Provider, Scope, provide
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Error, ErrorOptions, Feature, Inject, ServiceScope
class CleanupFailure(Exception): pass
cleanup = Error(CleanupFailure, status=409, code='cleanup', title='Cleanup failed')
class Resource: pass
class Work:
    def __init__(self, container: AsyncContainer): self.scope = ServiceScope(container, Resource)
    @asynccontextmanager
    async def __call__(self):
        async with self.scope() as resource:
            yield resource
class Resources(Provider):
    scope = Scope.REQUEST
    work = provide(Work, scope=Scope.APP)
    @provide
    def resource(self) -> Iterator[Resource]:
        yield Resource()
        raise CleanupFailure('finalizer failed')
router = APIRouter()
@router.get('/close', raises=[cleanup])
async def close(work: Inject[Work]):
    async with work():
        return {'ok': True}
feature = Feature(name='resources', routers=[router], providers=[Resources()])
app = FastAPI()
Composition(features=[feature], errors=ErrorOptions(type_base='urn:test:problem')).build(app)
"""),
        encoding="utf8",
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
    assert checked.returncode == 1, checked.stderr
    report = json.loads(checked.stdout)
    [operation] = report["operations"]
    assert not operation["complete"] and report["contract_gaps"] == 0
    assert any(
        d["code"] == "CAN003" and "Dishka ExitError" in d["message"]
        for d in operation["diagnostics"]
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
            "    response = client.get('/close')\n"
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
    assert response["status"] == 500
    assert response["body"]["code"] == "internal_server_error"
