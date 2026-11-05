import json
import os
import subprocess
import sys
from pathlib import Path


def test_cli_exports_without_runtime_secrets_and_checks_drift(tmp_path: Path) -> None:
    (tmp_path / "application.py").write_text(
        "import os\n"
        "from contextlib import asynccontextmanager\n"
        "from fastapi import FastAPI, Depends\n"
        "@asynccontextmanager\n"
        "async def lifespan(app):\n"
        "    assert os.environ['JWT_SECRET']\n"
        "    yield\n"
        "def authenticated():\n"
        "    return os.environ['JWT_SECRET']\n"
        "def create_app():\n"
        "    app = FastAPI(title='Büro', lifespan=lifespan)\n"
        "    @app.get('/protected', dependencies=[Depends(authenticated)])\n"
        "    def protected() -> str: return 'ok'\n"
        "    return app\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if key != "JWT_SECRET"}
    command = [
        sys.executable,
        "-m",
        "fastapi_canon",
        "contracts",
        "export",
        "--app",
        "application:create_app",
        "--factory",
        "--output-dir",
        "contracts",
    ]
    exported = subprocess.run(
        command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=45
    )
    assert exported.returncode == 0, exported.stderr
    path = tmp_path / "contracts/openapi.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["info"]["title"] == "Büro"
    assert "/protected" in document["paths"]
    assert not (tmp_path / "contracts/openrpc.json").exists()
    assert not (tmp_path / "contracts/webrtc.json").exists()
    original = path.read_bytes()
    checked = subprocess.run(
        [*command, "--check"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert checked.returncode == 0, checked.stderr
    path.write_bytes(b"{}\n")
    drift = subprocess.run(
        [*command, "--diff"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert drift.returncode == 1, drift.stderr
    assert "Out of date" in drift.stdout
    assert "+" in drift.stdout and "Büro" in drift.stdout
    assert path.read_bytes() == b"{}\n"
    assert b"B\xc3\xbcro" in original
    stale = tmp_path / "contracts/openrpc.json"
    stale.write_bytes(b"{}\n")
    unrelated = tmp_path / "contracts/notes.txt"
    unrelated.write_text("Keep this", encoding="utf-8")
    cleaned = subprocess.run(
        command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=45
    )
    assert cleaned.returncode == 0, cleaned.stderr
    assert not stale.exists()
    assert unrelated.read_text(encoding="utf-8") == "Keep this"


def test_cli_reports_import_time_configuration_errors_without_placeholder_secrets(
    tmp_path: Path,
) -> None:
    (tmp_path / "eager.py").write_text(
        "raise ValueError('JWT_SECRET required at import')\n", encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "contracts",
            "export",
            "--app",
            "eager:app",
            "--output-dir",
            "contracts",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 2
    assert "JWT_SECRET required at import" in result.stderr
    assert not (tmp_path / "contracts").exists()
