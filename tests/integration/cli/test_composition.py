import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def application_module(tmp_path: Path) -> Path:
    (tmp_path / "diagnostic_app.py").write_text(
        dedent("""
        from fastapi import FastAPI
        from fastapi_canon import Composition, Feature
        from fastapi_canon.diagnostics import CheckIssue
        from fastapi_canon.routing.http import CanonRouter

        def warning(context):
            return [CheckIssue("projects.W001", "Optional export missing", "Declare an export.", severity="warning")]

        router = CanonRouter(prefix="/projects")
        router.get("/{project_id}", operation_id="projects.get")(lambda project_id: project_id)
        composition = Composition(features=(Feature(name="projects", routers=[router], checks=[warning]),))

        def create_app():
            return composition.build(FastAPI())

        app = create_app()
    """),
        encoding="utf-8",
    )
    return tmp_path


def test_cli_inspect_uses_the_installed_snapshot_as_json(
    application_module: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "inspect",
            "diagnostic_app:app",
            "--format",
            "json",
        ],
        cwd=application_module,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["schema_version"] == 1
    assert document["operations"][0]["path"] == "/projects/{project_id}"
    assert document["operations"][0]["feature_name"] == "projects"
    assert document["operations"][0]["operation_id"] == "projects.get"


@pytest.mark.parametrize("options", [[], ["--factory"]])
def test_cli_inspect_accepts_a_factory_and_its_compatibility_flag(
    application_module: Path,
    options: list[str],
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "inspect",
            "diagnostic_app:create_app",
            *options,
        ],
        cwd=application_module,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Feature projects" in result.stdout
    assert "GET /projects/{project_id} [projects.get]" in result.stdout


@pytest.mark.parametrize(
    ("options", "expected"), [([], 0), (["--fail-on", "warning"], 1)]
)
def test_cli_check_warning_exit_policy(
    application_module: Path, options: list[str], expected: int
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "check",
            "composition",
            "diagnostic_app:composition",
            "--format",
            "json",
            *options,
        ],
        cwd=application_module,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == expected, result.stderr
    document = json.loads(result.stdout)
    assert document["complete"] is True
    assert document["has_errors"] is False
    assert document["issues"][0]["code"] == "projects.W001"
    assert document["issues"][0]["feature_names"] == ["projects"]


def test_cli_check_reports_a_conflict_before_application_installation(
    tmp_path: Path,
) -> None:
    (tmp_path / "invalid_app.py").write_text(
        dedent("""
        from fastapi_canon import Composition, Feature
        from fastapi_canon.routing.http import CanonRouter
        first = CanonRouter()
        second = CanonRouter()
        first.get("/first", operation_id="duplicate")(lambda: "first")
        second.get("/second", operation_id="duplicate")(lambda: "second")
        composition = Composition(features=(Feature(name="first", routers=[first]), Feature(name="second", routers=[second])))
    """),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "check",
            "composition",
            "invalid_app:composition",
            "--format",
            "json",
            "--require-complete",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["has_errors"] is True
    assert report["complete"] is False
    assert report["issues"][0]["feature_names"] == ["first", "second"]
    assert report["issues"][0]["locations"] == ["GET /first", "GET /second"]


@pytest.mark.parametrize(
    "target",
    [["downloads:composition"], ["--app", "downloads:app"]],
    ids=["preflight", "installed"],
)
def test_cli_warns_about_a_raw_get_with_no_documented_content(
    tmp_path: Path, target: list[str]
) -> None:
    (tmp_path / "downloads.py").write_text(
        dedent("""
        from fastapi import APIRouter as FastAPIRouter, FastAPI, Response
        from fastapi_canon import APIRouter, CanonResponse, Composition, Feature

        def download() -> Response:
            raise AssertionError("the check must not run the handler")

        native = FastAPIRouter()
        native.get("/ambiguous", operation_id="files.raw")(download)
        native.get("/documented", operation_id="files.native", responses={
            200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}
        })(download)
        native.get("/extra", operation_id="files.extra", openapi_extra={"responses": {
            "200": {"content": {"image/png": {}}}
        }})(download)
        native.get("/no-content", operation_id="files.no_content", status_code=205)(download)
        native.head("/head", operation_id="files.head")(download)
        native.get("/hidden", operation_id="files.hidden", include_in_schema=False)(download)

        router = APIRouter()
        router.include_router(native, prefix="/files", default_response_class=Response)
        router.get("/empty", operation_id="files.empty", response=CanonResponse.empty(status=200))(download)
        router.get("/binary", operation_id="files.binary", response=CanonResponse.binary("application/pdf"))(download)
        composition = Composition(features=[Feature(name="files", routers=[router])])
        app = composition.build(FastAPI())
        """),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "check",
            "composition",
            *target,
            "--fail-on",
            "warning",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["complete"] is True
    assert report["has_errors"] is False
    (issue,) = report["issues"]
    assert issue["code"] == "canon.contracts.W001"
    assert issue["severity"] == "warning"
    assert issue["locations"] == ["GET /files/ambiguous"]
    assert "files.raw" in issue["message"]
    assert "200" in issue["message"]
    assert "content" in issue["message"]
    assert 'CanonResponse.binary("*/*", status=200)' in issue["hint"]


@pytest.mark.parametrize(
    ("target", "status", "events"),
    [
        ("create_app", 0, ["released", "finalized"]),
        ("conflicting_app", 2, ["released", "finalized"]),
        ("failing_app", 2, ["released", "finalized"]),
        ("app", 0, []),
        ("bare", 2, []),
    ],
)
def test_cli_checks_installed_apps_and_releases_only_factory_resources(
    tmp_path: Path, target: str, status: int, events: list[str]
) -> None:
    (tmp_path / "owned.py").write_text(
        dedent("""
        import asyncio
        from collections.abc import Iterator
        from contextlib import asynccontextmanager
        from pathlib import Path
        from dishka import Provider, Scope, provide
        from fastapi import FastAPI
        from fastapi_canon import Composition, Feature, check_app
        from fastapi_canon.integration import Integration

        def record(event):
            with Path("events").open("a") as out:
                print(event, file=out)

        class Resources(Provider):
            @provide(scope=Scope.APP)
            def resource(self) -> Iterator[str]:
                yield "available"
                record("released")

        class Cleanup(Integration):
            def finalizer(self):
                return self.close

            def close(self):
                record("finalized")

        class FailingCleanup(Cleanup):
            def close(self):
                super().close()
                raise RuntimeError("cleanup failed")

        @asynccontextmanager
        async def lifespan(app):
            record("started")
            yield

        def create_app(cleanup=Cleanup):
            feature = Feature(name="test", providers=[Resources()])
            app = Composition(features=[feature], integrations=[cleanup()]).build(
                FastAPI(lifespan=lifespan)
            )
            assert asyncio.run(app.state.dishka_container.get(str)) == "available"
            return app

        def conflicting_app():
            app = create_app()
            app.get("/first", operation_id="duplicate")(lambda: 1)
            app.get("/second", operation_id="duplicate")(lambda: 2)
            return app

        def failing_app():
            return create_app(FailingCleanup)

        bare = FastAPI()
        app = create_app()
        assert check_app(app).complete
        assert asyncio.run(app.state.dishka_container.get(str)) == "available"
        """),
        encoding="utf-8",
    )
    log = tmp_path / "events"
    log.touch()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "check",
            "composition",
            "--app",
            f"owned:{target}",
            "--require-complete",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == status, result.stdout + result.stderr
    report = json.loads(result.stdout)
    if target == "bare":
        assert "Build a Composition" in report["error"]
    elif target == "failing_app":
        assert report == {
            "error": "Cannot close app from factory 'owned:failing_app' (RuntimeError)"
        }
        assert result.stderr == ""
    else:
        assert report["schema_version"] == 1
        assert report["complete"] == (status == 0)
        assert report["has_errors"] == (status != 0)
    assert log.read_text().splitlines() == events


def test_cli_rejects_targets_without_an_installed_composition(
    application_module: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "inspect",
            "diagnostic_app:composition",
            "--format",
            "json",
        ],
        cwd=application_module,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "FastAPI application" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("namespace", [None, "projects"])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_cli_reports_automatic_http_sse_id_collisions(
    tmp_path: Path, namespace: str | None, output_format: str
) -> None:
    (tmp_path / "collision.py").write_text(
        dedent(f"""
        from collections.abc import AsyncIterator
        from fastapi_canon import APIRouter, Composition, Feature

        first = APIRouter(namespace={namespace!r}, prefix="/projects")
        second = APIRouter(namespace={namespace!r}, prefix="/archive")
        @first.get("/current")
        def events() -> str:
            return "current"
        @second.sse("/events")
        async def events() -> AsyncIterator[str]:
            yield "updated"
        parent = APIRouter(namespace="ignored", prefix="/v1")
        parent.include_router(second)
        composition = Composition(features=[
            Feature(name="projects", routers=[first]),
            Feature(name="archive", routers=[parent]),
        ])
        """),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "check",
            "composition",
            "collision:composition",
            "--format",
            output_format,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    identity = "projects.events" if namespace is not None else "events"
    if output_format == "json":
        report = json.loads(result.stdout)
        assert report["has_errors"] is True
        (issue,) = report["issues"]
        assert issue["code"] == "canon.routing.E002"
        assert issue["message"] == f"Duplicate operation ID {identity!r}"
        assert issue["feature_names"] == ["projects", "archive"]
        assert issue["locations"] == ["GET /projects/current", "GET /v1/archive/events"]
        assert "operation_id" in issue["hint"]
    else:
        assert (
            f"canon.routing.E002: Duplicate operation ID {identity!r}" in result.stdout
        )
        assert "GET /projects/current" in result.stdout
        assert "GET /v1/archive/events" in result.stdout
        assert "operation_id" in result.stdout
