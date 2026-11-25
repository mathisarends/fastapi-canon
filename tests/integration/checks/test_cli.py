import json
import runpy
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from fastapi.testclient import TestClient

import fastapi_canon.cli as canon_cli


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


SHARED = """
from abc import ABC, abstractmethod
from dishka import Provider, Scope, provide
from fastapi_canon import APIRouter, Error

class Missing(Exception): pass
class Conflict(Exception): pass

missing = Error(Missing, status=404, code='missing', title='Missing')
conflict = Error(Conflict, status=409, code='conflict', title='Conflict')

class Repository(ABC):
    @abstractmethod
    async def get(self) -> None: ...

class Service:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    async def run(self) -> None:
        await self._repository.get()

class ConflictingRepository(Repository):
    async def get(self) -> None:
        raise Conflict()

class SharedProvider(Provider):
    repository = provide(ConflictingRepository, provides=Repository, scope=Scope.REQUEST)

router = APIRouter(raises=[conflict])

@router.get('/shared')
async def shared_endpoint() -> None:
    raise Missing()
"""

APPLICATION = """
from dishka import Provider, Scope, provide
from fastapi_canon import APIRouter, Inject
from shared import Conflict, Missing, Repository, Service, missing

class {name}Repository(Repository):
    async def get(self) -> None:
        raise {error}()

class {name}Provider(Provider):
    repository = provide({name}Repository, provides=Repository, scope=Scope.REQUEST)

router = APIRouter(raises=[missing])

@router.get('/')
async def endpoint(service: Inject[Service]) -> None:
    await service.run()
"""


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Two applications that bind one shared port to different implementations."""
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "shared.py").write_text(dedent(SHARED), encoding="utf-8")
    for name, error in (("alpha", "Missing"), ("beta", "Conflict")):
        source = tmp_path / name / "src"
        source.mkdir(parents=True)
        (source / f"{name}_app.py").write_text(
            dedent(APPLICATION.format(name=name.title(), error=error)),
            encoding="utf-8",
        )
        (tmp_path / name / "fastapi-canon.toml").write_text(
            dedent("""
            [checks.errors]
            roots = ["src"]
            reference-roots = ["../shared"]
            require-complete = true
            """),
            encoding="utf-8",
        )
    return tmp_path


def test_configured_applications_keep_their_bindings_and_report_together(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = canon_cli.main(
        [
            "check",
            "errors",
            "--config",
            str(repository / "beta" / "fastapi-canon.toml"),
            "--config",
            str(repository / "alpha" / "fastapi-canon.toml"),
            "--strict",
            "--format",
            "json",
        ]
    )

    document = json.loads(capsys.readouterr().out)
    assert status == 1
    assert document["schema_version"] == 1
    reports = {item["name"]: item["report"] for item in document["applications"]}
    assert list(reports) == ["beta", "alpha"]
    [beta] = reports["beta"]["operations"]
    [alpha] = reports["alpha"]["operations"]
    assert beta["function"] == "beta_app.endpoint"
    assert [item["code"] for item in beta["diagnostics"]] == ["CAN002"]
    assert alpha["function"] == "alpha_app.endpoint"
    assert alpha["complete"]
    assert alpha["diagnostics"] == []


def test_reference_root_resolves_definitions_from_the_command_line(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = canon_cli.main(
        [
            "check",
            "errors",
            str(repository / "alpha" / "src"),
            "--reference-root",
            str(repository / "shared"),
            "--strict",
            "--require-complete",
        ]
    )

    assert status == 0
    assert "Analyzed 1 operations; 0 contract gaps; 0 incomplete operations." in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize(
    ("arguments", "setting", "cause"),
    [
        (["alpha/src"], "", "cannot be combined"),
        ([], 'unknown = "x"', "unknown settings in checks.errors: unknown"),
        ([], 'roots = ["absent"]', "is not a directory"),
        ([], 'reference-roots = ["src"]', "duplicate module 'alpha_app'"),
        (["--config", "beta/fastapi-canon.toml"], "", "duplicate application name"),
    ],
)
def test_invalid_configured_checks_exit_two_and_name_the_cause(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    setting: str,
    cause: str,
) -> None:
    monkeypatch.chdir(repository)
    if setting:
        key = setting.partition(" ")[0]
        lines = [
            line
            for line in (repository / "alpha" / "fastapi-canon.toml")
            .read_text(encoding="utf-8")
            .splitlines()
            if not line.startswith(f"{key} ")
        ]
        (repository / "alpha" / "fastapi-canon.toml").write_text(
            "\n".join([*lines, setting]), encoding="utf-8"
        )
    (repository / "beta" / "fastapi-canon.toml").write_text(
        'name = "alpha"\n'
        + (repository / "beta" / "fastapi-canon.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    try:
        status = canon_cli.main(
            ["check", "errors", "--config", "alpha/fastapi-canon.toml", *arguments]
        )
    except SystemExit as exited:
        status = exited.code

    assert status == 2
    assert cause in capsys.readouterr().err
