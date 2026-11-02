import subprocess
import sys
from pathlib import Path


def test_installed_command_creates_and_applies_real_revisions(tmp_path: Path) -> None:
    configuration = tmp_path / "pyproject.toml"
    configuration.write_text(
        '[tool.fastapi-canon.database]\ntarget = "db_config:database"\n',
        encoding="utf-8",
    )
    (tmp_path / "db_config.py").write_text(
        "from sqlalchemy import MetaData, Table, Column, String\n"
        "from fastapi_canon import Database, AlembicMigrations\n"
        "metadata = MetaData()\n"
        'Table("items", metadata, Column("name", String, primary_key=True))\n'
        f"database = Database(url={'sqlite:///' + str(tmp_path / 'cli.db')!r}, "
        "migrations=AlembicMigrations(directory='migrations', metadata=metadata))\n",
        encoding="utf-8",
    )
    # Invoke from another cwd to verify config-relative module and path resolution.
    command = [
        sys.executable,
        "-m",
        "fastapi_canon",
        "db",
        "--config",
        str(configuration),
    ]
    initialized = subprocess.run(
        [*command, "init"],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert initialized.returncode == 0, initialized.stderr
    revision = subprocess.run(
        [*command, "revision", "--autogenerate", "-m", "initial"],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert revision.returncode == 0, revision.stderr
    pending = subprocess.run(
        [*command, "check"],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert pending.returncode == 2
    assert "pending" in pending.stderr
    upgraded = subprocess.run(
        [*command, "upgrade", "head"],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    checked = subprocess.run(
        [*command, "check"],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert checked.returncode == 0, checked.stderr
    assert len(list((tmp_path / "migrations/versions").glob("*.py"))) == 1
