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
        from fastapi_canon import CanonRouter, CheckIssue, Composition, Feature

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


def test_cli_inspect_calls_a_factory_only_when_requested(
    application_module: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "inspect",
            "diagnostic_app:create_app",
            "--factory",
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
        from fastapi_canon import CanonRouter, Composition, Feature
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
