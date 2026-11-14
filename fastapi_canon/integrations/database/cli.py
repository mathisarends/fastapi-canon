"""Database commands; optional runtimes are loaded after argument parsing."""

import argparse
import asyncio
import importlib
import os
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Manage database revisions with Alembic."""
    parser = argparse.ArgumentParser(prog="fastapi-canon db")
    parser.add_argument("--config", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--database", help="Override the configured module:attribute")
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
        module, attribute = target.split(":")
        database = getattr(importlib.import_module(module), attribute)
        from fastapi_canon.integrations.database import Database

        if not isinstance(database, Database) or database.migrations is None:
            raise ValueError("Target must be a Database with AlembicMigrations")
        migrations = database.migrations
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
    except Exception as error:
        print(f"Database command failed: {error}", file=sys.stderr)
        return 2
    finally:
        os.chdir(original_cwd)
        sys.path[:] = original_path
