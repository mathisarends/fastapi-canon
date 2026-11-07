"""Structured common generation through real configurations and artifacts."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    example = Path(__file__).parents[3] / "examples/canonical_errors"
    shutil.copy2(example / "fastapi-canon.toml", tmp_path)
    shutil.copytree(example / "contracts", tmp_path / "contracts")
    source = (example / "app.py").read_text(encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "print('private application stdout')\n" + source, encoding="utf-8"
    )
    return tmp_path


def test_json_generation_and_clean_check_report_source_warnings(project: Path) -> None:
    generated = _run(project)
    assert generated.returncode == 0, generated.stderr
    report = json.loads(generated.stdout)
    assert report["schema_version"] == 1
    assert report["command"] == "generate"
    assert report["status"] == "success" and report["changed"] is True
    assert report["check"] is False and report["offline"] is False
    assert report["targets"] == ["browser", "python"]
    assert report["changes"]
    assert report["diff"] is None
    warnings = [d for d in report["diagnostics"] if d["code"] == "canon.codegen.W002"]
    assert warnings
    assert warnings[0]["severity"] == "warning"
    assert warnings[0]["locations"][0]["source"] == "api"
    assert warnings[0]["locations"][0]["operation"] == "projects.events"
    assert warnings[0]["locations"][0]["target"] == "browser"
    assert warnings[0]["locations"][0]["schema_pointer"].startswith("/components/")
    assert any(d["code"] == "canon.codegen.I001" for d in report["diagnostics"])
    assert "private application stdout" not in generated.stdout + generated.stderr
    assert not generated.stderr
    before = _artifacts(project)
    checked = _run(project, "--check", "--offline")
    assert checked.returncode == 0, checked.stderr
    report = json.loads(checked.stdout)
    assert report["status"] == "success" and report["changed"] is False
    assert report["changes"] == []
    assert report["offline"] and report["check"]
    assert {d["code"] for d in report["diagnostics"]} == {"canon.codegen.W002"}
    assert _artifacts(project) == before


@pytest.mark.parametrize("option", ["--check", "--diff"])
def test_json_drift_is_read_only_and_separates_diff(project: Path, option: str) -> None:
    assert _run(project).returncode == 0
    changed = project / "dist/generated/typescript/client.ts"
    changed.write_text(
        changed.read_text(encoding="utf-8") + "// local edit\n", encoding="utf-8"
    )
    before = _artifacts(project)
    result = _run(project, option, "--target", "browser")
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == 1
    assert report["status"] == "drift" and report["check"]
    assert report["targets"] == ["browser"]
    assert report["changes"] == [str(changed)]
    drift = next(d for d in report["diagnostics"] if d["code"] == "canon.codegen.W001")
    assert drift["severity"] == "warning"
    assert drift["locations"] == [{"path": str(changed)}]
    if option == "--diff":
        assert "// local edit" in report["diff"]
    else:
        assert report["diff"] is None
    assert _artifacts(project) == before


def test_json_unsupported_operation_has_context_without_partial_writes(
    project: Path,
) -> None:
    document = {
        "openapi": "3.2.0",
        "info": {"title": "Upload", "version": "1"},
        "paths": {
            "/upload": {
                "post": {
                    "operationId": "files.upload",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "description": "private schema value",
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    (project / "upload.json").write_text(json.dumps(document), encoding="utf-8")
    configuration = project / "fastapi-canon.toml"
    configuration.write_text(
        configuration.read_text(encoding="utf-8")
        + (
            '\n[codegen.sources.upload]\nkind = "openapi"\ninput = "upload.json"\n'
            '[codegen.targets.unsupported]\nlanguage = "python"\nsources = ["upload"]\n'
            'output = "bad-sdk"\npackage = "bad_sdk"\n'
        ),
        encoding="utf-8",
    )
    before = _artifacts(project)
    result = _run(project)
    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "error" and report["changed"] is None
    assert report["changes"] == []
    assert report["targets"] == ["browser", "python", "unsupported"]
    (diagnostic,) = report["diagnostics"]
    assert diagnostic["severity"] == "error"
    assert diagnostic["code"] == "canon.codegen.E002"
    assert "multipart requires a flat object" in diagnostic["message"]
    assert diagnostic["locations"] == [
        {
            "source": "upload",
            "operation": "files.upload",
            "path": "POST /upload",
            "target": "unsupported",
        }
    ]
    assert "private schema value" not in result.stdout
    assert "private application stdout" not in result.stdout + result.stderr
    assert _artifacts(project) == before
    assert not (project / "bad-sdk").exists()


@pytest.mark.parametrize("failure", ["configuration", "import", "target"])
def test_json_preflight_errors_keep_exit_two_and_do_not_write(
    project: Path, failure: str
) -> None:
    options: list[str] = []
    if failure == "configuration":
        (project / "fastapi-canon.toml").write_text(
            "[codegen]\nversion = 2\n", encoding="utf-8"
        )
    elif failure == "import":
        (project / "app.py").write_text(
            "raise RuntimeError('private configuration value')\n", encoding="utf-8"
        )
    else:
        options = ["--target", "unknown"]
    before = _artifacts(project)
    result = _run(project, *options)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["schema_version"] == 1 and report["status"] == "error"
    (diagnostic,) = report["diagnostics"]
    assert diagnostic["severity"] == "error"
    assert diagnostic["code"] == "canon.codegen.E001"
    assert diagnostic["message"]
    assert "private configuration value" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    if failure == "import":
        assert diagnostic["locations"] == [{"source": "api"}]
        assert "Cannot import" in diagnostic["message"]
    elif failure == "target":
        assert report["targets"] == ["unknown"]
    assert _artifacts(project) == before


def test_json_empty_contract_clean_success_has_no_diagnostics(tmp_path: Path) -> None:
    (tmp_path / "api.json").write_text(
        json.dumps(
            {
                "openapi": "3.2.0",
                "info": {"title": "Empty", "version": "1"},
                "paths": {},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "fastapi-canon.toml").write_text(
        '[codegen]\nversion = 1\n[codegen.sources.api]\nkind = "openapi"\ninput = "api.json"\n'
        '[codegen.targets.python]\nlanguage = "python"\nsources = ["api"]\n'
        'output = "sdk"\npackage = "sdk"\n',
        encoding="utf-8",
    )
    assert _run(tmp_path).returncode == 0
    checked = _run(tmp_path, "--check")
    assert checked.returncode == 0, checked.stderr
    report = json.loads(checked.stdout)
    assert report["status"] == "success" and report["changed"] is False
    assert report["diagnostics"] == []
    assert report["changes"] == []


def _run(project: Path, *options: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "generate",
            "--format",
            "json",
            *options,
        ],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )


def _artifacts(project: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(project)): path.read_bytes()
        for directory in ("contracts", "dist", "sdk", "bad-sdk")
        for path in (project / directory).rglob("*")
        if path.is_file()
    }
