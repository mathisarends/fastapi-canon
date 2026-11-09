import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    example = Path(__file__).parents[3] / "examples/canonical_errors"
    (tmp_path / "application.py").write_text(
        "print('private app output')\n"
        + (example / "app.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "fastapi-canon.toml").write_text(
        """[codegen]
version = 2
app = "application:create_app"
contracts = "contracts"
[codegen.targets.streams]
generator = "sse"
language = "python"
output = "streams"
[codegen.targets.rpc]
generator = "websockets"
language = "python"
output = "rpc"
package = "rpc"
client-name = "ProjectsClient"
""",
        encoding="utf-8",
    )
    return tmp_path


def test_generate_and_read_only_check_use_specialized_manifests(project: Path) -> None:
    generated = _run(project)
    assert generated.returncode == 0, generated.stderr + generated.stdout
    report = json.loads(generated.stdout)
    assert report["schema_version"] == 2
    assert [step["name"] for step in report["steps"]] == ["contracts", "streams", "rpc"]
    assert "private app output" not in generated.stdout + generated.stderr
    assert (project / "streams/.fastapi-canon-sse.json").is_file()
    assert (project / "rpc/.rpcgen/manifest.json").is_file()
    assert (project / "rpc/namespaces/projects.py").is_file()
    before = _artifacts(project)
    checked = _run(project, "--check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert all(
        step["status"] == "current" for step in json.loads(checked.stdout)["steps"]
    )
    assert _artifacts(project) == before


@pytest.mark.parametrize(
    "target,filename", [("streams", "models.py"), ("rpc", "models.py")]
)
def test_diff_selects_one_target_and_preserves_all_files(
    project: Path, target: str, filename: str
) -> None:
    assert _run(project).returncode == 0
    path = project / target / filename
    path.write_text(
        path.read_text(encoding="utf-8") + "# manual drift\n", encoding="utf-8"
    )
    before = _artifacts(project)
    checked = _run(project, "--diff", "--target", target)
    assert checked.returncode == 1, checked.stdout + checked.stderr
    report = json.loads(checked.stdout)
    assert [step["name"] for step in report["steps"]] == ["contracts", target]
    assert "manual drift" in report["steps"][1]["diff"]
    assert _artifacts(project) == before


def test_contract_drift_blocks_clients_and_removes_stale_contract_on_generation(
    project: Path,
) -> None:
    assert _run(project).returncode == 0
    path = project / "contracts/openapi.json"
    path.write_text("{}\n", encoding="utf-8")
    stale = project / "contracts/webrtc.json"
    stale.write_text("{}\n", encoding="utf-8")
    before = _artifacts(project)
    checked = _run(project, "--diff")
    assert checked.returncode == 1
    report = json.loads(checked.stdout)
    assert report["steps"][0]["status"] == "drift"
    assert [step["status"] for step in report["steps"][1:]] == ["blocked", "blocked"]
    assert _artifacts(project) == before
    assert _run(project).returncode == 0
    assert not stale.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["openapi"].startswith("3.")


def test_offline_does_not_import_the_application(project: Path) -> None:
    assert _run(project).returncode == 0
    (project / "application.py").write_text(
        "raise RuntimeError('do not import')\n", encoding="utf-8"
    )
    checked = _run(project, "--offline", "--check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert (project / "rpc/client.py").is_file()


def test_external_commands_run_from_config_root_and_report_drift(project: Path) -> None:
    (project / "external.py").write_text(
        "import sys\nfrom pathlib import Path\np = Path('external-client.txt')\nif sys.argv[1] == 'generate':\n    p.write_text('generated')\nelse:\n    raise SystemExit(0 if p.read_text() == 'generated' else 1)\n",
        encoding="utf-8",
    )
    _external_config(project, "http", "external.py")
    assert _run(project).returncode == 0
    path = project / "external-client.txt"
    assert path.read_text() == "generated"
    path.write_text("drift")
    before = _artifacts(project)
    checked = _run(project, "--check", "--target", "http")
    assert checked.returncode == 1
    assert json.loads(checked.stdout)["steps"][-1]["status"] == "drift"
    assert _artifacts(project) == before


def test_external_error_does_not_hide_other_target_results(project: Path) -> None:
    (project / "failing.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
    _external_config(project, "http", "failing.py")
    result = _run(project)
    assert result.returncode == 2
    steps = json.loads(result.stdout)["steps"]
    assert steps[-1]["status"] == "error"
    assert steps[1]["status"] == "updated"
    assert (project / "rpc/client.py").is_file()


def test_old_configuration_fails_before_writing(project: Path) -> None:
    path = project / "fastapi-canon.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("version = 2", "version = 1"),
        encoding="utf-8",
    )
    result = _run(project)
    assert result.returncode == 2
    assert (
        "version 1 domain SDK generation was removed"
        in json.loads(result.stdout)["error"]
    )
    assert not (project / "contracts").exists()


def test_native_preflight_failure_leaves_contracts_and_clients_unwritten(
    project: Path,
) -> None:
    with (project / "fastapi-canon.toml").open("a", encoding="utf-8") as file:
        file.write(
            '\n[codegen.targets.video]\ngenerator = "webrtc"\noutput = "video"\n'
        )
    result = _run(project)
    assert result.returncode == 2
    assert "registered webrtc contract" in json.loads(result.stdout)["error"]
    assert not (project / "contracts").exists()
    assert not (project / "streams").exists()
    assert not (project / "rpc").exists()


def test_specialized_ownership_removes_stale_files_and_preserves_handwritten_files(
    project: Path,
) -> None:
    assert _run(project).returncode == 0
    output = project / "streams"
    manifest = output / ".fastapi-canon-sse.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["files"].append("obsolete.py")
    manifest.write_text(json.dumps(value), encoding="utf-8")
    stale = output / "obsolete.py"
    stale.write_text("# obsolete\n", encoding="utf-8")
    handwritten = output / "notes.txt"
    handwritten.write_text("keep me", encoding="utf-8")
    before = _artifacts(project)
    assert _run(project, "--check", "--target", "streams").returncode == 1
    assert _artifacts(project) == before
    assert _run(project, "--target", "streams").returncode == 0
    assert not stale.exists()
    assert handwritten.read_text(encoding="utf-8") == "keep me"


def test_offline_webrtc_target_generates_and_checks_specialized_client(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[3]
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "webrtc.json").write_bytes(
        (root / "examples/webrtc/practice/contract.json").read_bytes()
    )
    (tmp_path / "fastapi-canon.toml").write_text(
        '[codegen]\nversion = 2\napp = "unused:app"\n[codegen.targets.video]\ngenerator = "webrtc"\noutput = "video"\n',
        encoding="utf-8",
    )
    result = _run(tmp_path, "--offline")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "video/client.ts").is_file()
    assert (tmp_path / "video/.fastapi-webrtc.json").is_file()
    assert _run(tmp_path, "--offline", "--check").returncode == 0


def _external_config(project: Path, name: str, script: str) -> None:
    with (project / "fastapi-canon.toml").open("a", encoding="utf-8") as file:
        file.write(
            f'\n[codegen.targets.{name}]\ngenerator = "external"\ngenerate-command = {json.dumps([sys.executable, script, "generate"])}\ncheck-command = {json.dumps([sys.executable, script, "check"])}\n'
        )


def _run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "generate",
            "--format",
            "json",
            *arguments,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _artifacts(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
