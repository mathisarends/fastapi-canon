import argparse
import asyncio
import importlib
import os
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi_canon.integrations.database.database import Database
    from fastapi_canon.integrations.database.migrations import AlembicMigrations


def main(argv: Sequence[str] | None = None) -> int:
    """Manage database revisions with Alembic."""
    parser = argparse.ArgumentParser(prog="fastapi-canon db")
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    parser.add_argument(
        "--database",
        help="Override the configured module:attribute of an app or Database",
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("init", help="Create the Alembic revision directory")
    revision = commands.add_parser(
        "revision", help="Create a reviewable Alembic revision"
    )
    revision.add_argument("--autogenerate", action="store_true")
    revision.add_argument("-m", "--message", required=True)
    upgrade = commands.add_parser("upgrade", help="Apply Alembic revisions")
    upgrade.add_argument("revision", nargs="?", default="head")
    downgrade = commands.add_parser(
        "downgrade", help="Revert Alembic revisions to an explicit target"
    )
    downgrade.add_argument("revision")
    commands.add_parser("check", help="Check revisions and model/schema drift")
    args = parser.parse_args(argv)
    original_cwd = Path.cwd()
    original_path = sys.path.copy()
    try:
        config_path = args.config.resolve()
        target = args.database
        if target is None:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            target = config["tool"]["fastapi-canon"]["database"]["target"]
        if not isinstance(target, str) or target.count(":") != 1:
            raise ValueError("Database target must be module:attribute")
        # App-relative configuration works independently of the invoking cwd.
        os.chdir(config_path.parent)
        sys.path.insert(0, str(config_path.parent))
        database, migrations = _resolve(target)
        if args.operation == "init":
            migrations.initialize()
        else:

            async def operate() -> None:
                async with database.open_engine() as engine:
                    match args.operation:
                        case "upgrade":
                            await migrations.upgrade(engine, args.revision)
                        case "downgrade":
                            await migrations.downgrade(engine, args.revision)
                        case "revision":
                            await migrations.revision(
                                engine,
                                message=args.message,
                                autogenerate=args.autogenerate,
                            )
                        case "check":
                            await migrations.require_current(engine)
                            await migrations.check(engine)

            asyncio.run(operate())
        return 0
    # Application loading and database drivers have independent errors; normalize them
    # at the CLI boundary.
    except Exception as error:
        print(f"Database command failed: {error}", file=sys.stderr)
        return 2
    finally:
        os.chdir(original_cwd)
        sys.path[:] = original_path


def _resolve(target: str) -> tuple[Database, AlembicMigrations]:
    # lazy database runtime
    from fastapi import FastAPI

    from fastapi_canon.integrations.database.database import (
        Database,
        installed_database,
    )

    module, attribute = target.split(":")
    value = getattr(importlib.import_module(module), attribute)
    if isinstance(value, Database):
        database, migrations = value, value.migrations
    else:
        # An application supplies the tables its features contribute.
        app = value() if callable(value) and not isinstance(value, FastAPI) else value
        installed = installed_database(app) if isinstance(app, FastAPI) else None
        if installed is None:
            raise ValueError(
                "Target must be a Database, or an application or application "
                "factory whose Composition enables one"
            )
        database, migrations = installed.database, installed.migrations
    if migrations is None:
        raise ValueError("Target must declare a Database with AlembicMigrations")
    return database, migrations
