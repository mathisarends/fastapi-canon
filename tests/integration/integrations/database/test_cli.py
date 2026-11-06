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
        "from contextlib import contextmanager\n"
        "from pathlib import Path\n"
        "from sqlalchemy import MetaData, Table, Column, String, event\n"
        "from fastapi_canon.integrations.database import Database, AlembicMigrations\n"
        "def record(message):\n"
        "    with Path('engine-lifecycle.txt').open('a') as output:\n"
        "        output.write(message + '\\n')\n"
        "@contextmanager\n"
        "def engine_context(engine):\n"
        "    event.listen(engine.sync_engine, 'engine_disposed', "
        "lambda engine: record('dispose'))\n"
        "    record('enter')\n"
        "    try:\n"
        "        yield\n"
        "    finally:\n"
        "        record('exit')\n"
        "metadata = MetaData()\n"
        'Table("items", metadata, Column("name", String, primary_key=True))\n'
        f"database = Database(url={'sqlite:///' + str(tmp_path / 'cli.db')!r}, "
        "check_migrations_on_startup=False, "
        "engine_context=engine_context, "
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
    downgraded = subprocess.run(
        [*command, "downgrade", "base"],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    behind = subprocess.run(
        [*command, "check"],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert behind.returncode == 2
    assert "pending" in behind.stderr
    restored = subprocess.run(
        [*command, "upgrade", "head"],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert restored.returncode == 0, restored.stderr
    missing_target = subprocess.run(
        [*command, "downgrade"],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert missing_target.returncode == 2
    assert "revision" in missing_target.stderr
    invalid_target = subprocess.run(
        [*command, "downgrade", "unknown_revision"],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert invalid_target.returncode == 2
    assert "unknown_revision" in invalid_target.stderr
    assert (tmp_path / "engine-lifecycle.txt").read_text().splitlines() == [
        "enter",
        "exit",
        "dispose",
    ] * 8
    assert len(list((tmp_path / "migrations/versions").glob("*.py"))) == 1
