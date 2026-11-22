import sqlite3
import subprocess
import sys
from contextlib import closing
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


def test_application_target_migrates_the_tables_its_features_contribute(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.fastapi-canon.database]\ntarget = "db_app:create_app"\n',
        encoding="utf-8",
    )
    (tmp_path / "db_app.py").write_text(
        "from fastapi import FastAPI\n"
        "from sqlalchemy import Column, MetaData, String, Table\n"
        "from fastapi_canon import Composition, Feature\n"
        "from fastapi_canon.integrations.database import (\n"
        "    AlembicMigrations, Database, Tables)\n"
        "metadata = MetaData()\n"
        'items = Table("items", metadata, Column("name", String, primary_key=True))\n'
        'notes = Table("notes", metadata, Column("text", String, primary_key=True))\n'
        f"database = Database(url={'sqlite:///' + str(tmp_path / 'app.db')!r}, "
        "migrations=AlembicMigrations(directory='migrations'))\n"
        "def create_app():\n"
        "    return Composition(\n"
        "        features=[\n"
        "            Feature(name='items', integrations=[Tables(items)]),\n"
        "            Feature(name='notes', integrations=[Tables(notes)]),\n"
        "        ],\n"
        "        integrations=[database],\n"
        "    ).build(FastAPI())\n"
        "def plain_app():\n"
        "    return FastAPI()\n",
        encoding="utf-8",
    )

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "fastapi_canon", "db", *arguments],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def succeed(*arguments: str) -> None:
        completed = run(*arguments)
        assert completed.returncode == 0, completed.stderr

    succeed("init")
    succeed("revision", "--autogenerate", "-m", "initial")
    succeed("upgrade", "head")
    succeed("check")
    with closing(sqlite3.connect(tmp_path / "app.db")) as connection:
        tables = {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"items", "notes"} <= tables
    without_tables = run("--database", "db_app:database", "check")
    assert without_tables.returncode == 2
    assert "no metadata" in without_tables.stderr
    without_database = run("--database", "db_app:plain_app", "check")
    assert without_database.returncode == 2
    assert "Composition enables one" in without_database.stderr
