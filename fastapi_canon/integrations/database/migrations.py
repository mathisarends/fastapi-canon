"""Alembic revision files with Canon's database configuration and async engines."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Connection, MetaData, inspect
from sqlalchemy.ext.asyncio import AsyncEngine

if TYPE_CHECKING:
    from alembic.config import Config


@dataclass(frozen=True, slots=True)
class AlembicMigrations:
    """Application-owned revisions and explicitly registered model metadata.

    Relative directories resolve when the declaration is created. Prefer a path
    based on the configuration module for applications started from another cwd.
    """

    directory: Path
    metadata: MetaData

    def __init__(self, *, directory: str | Path, metadata: MetaData) -> None:
        object.__setattr__(self, "directory", Path(directory).resolve())
        object.__setattr__(self, "metadata", metadata)

    def initialize(self) -> None:
        """Create an Alembic script directory, preserving existing files."""
        _require_alembic()
        if self.directory.exists() and any(self.directory.iterdir()):
            raise ValueError("Migration directory must be empty for db init")
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "versions").mkdir(exist_ok=True)
        template = resources.files("alembic").joinpath(
            "templates/generic/script.py.mako"
        )
        (self.directory / "script.py.mako").write_text(
            template.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (self.directory / "env.py").write_text(
            "from fastapi_canon import run_alembic_environment\n\n"
            "run_alembic_environment()\n",
            encoding="utf-8",
        )

    async def upgrade(self, engine: AsyncEngine, revision: str = "head") -> None:
        from alembic import command

        def apply(config: Config) -> None:
            command.upgrade(config, revision)
            if revision in {"head", "heads"}:
                _require_schema(config.attributes["connection"], self.metadata)

        await self._run(engine, apply)

    async def downgrade(self, engine: AsyncEngine, revision: str) -> None:
        """Apply application-owned downgrades to an explicit Alembic target.

        Older targets need not match current model metadata. Startup and check
        retain their separate requirement that the application is at its heads.
        """
        from alembic import command

        await self._run(engine, lambda config: command.downgrade(config, revision))

    async def revision(
        self, engine: AsyncEngine, *, message: str, autogenerate: bool = False
    ) -> None:
        from alembic import command

        await self._run(
            engine,
            lambda config: command.revision(
                config, message=message, autogenerate=autogenerate
            ),
        )

    async def check(self, engine: AsyncEngine) -> None:
        """Fail when autogenerate would produce new migration operations."""
        from alembic import command

        await self._run(engine, command.check)

    async def require_current(self, engine: AsyncEngine) -> None:
        """Require the database revision set to match the application's heads."""
        _require_alembic()
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        expected = set(ScriptDirectory.from_config(self._config()).get_heads())
        if not expected:
            raise RuntimeError("No migrations found; create an initial revision")
        async with engine.connect() as connection:
            actual = await connection.run_sync(
                lambda sync: set(MigrationContext.configure(sync).get_current_heads())
            )
            if actual != expected:
                raise RuntimeError(
                    "Database migrations are pending or incompatible; "
                    "run fastapi-canon db upgrade head before starting the application"
                )
            await connection.run_sync(_require_schema, self.metadata)

    async def _run(
        self, engine: AsyncEngine, operation: Callable[[Config], object]
    ) -> None:
        config = self._config()
        async with engine.connect() as connection:
            sqlite = connection.dialect.name == "sqlite"
            if sqlite:
                await connection.run_sync(_sqlite_foreign_keys, False)
            try:
                async with connection.begin():
                    await connection.run_sync(_run_operation, config, operation)
                    if sqlite:
                        violations = await connection.exec_driver_sql(
                            "PRAGMA foreign_key_check"
                        )
                        if violations.first() is not None:
                            raise RuntimeError(
                                "Migration left invalid SQLite foreign keys"
                            )
            finally:
                if sqlite:
                    await connection.run_sync(_sqlite_foreign_keys, True)

    def _config(self) -> Config:
        _require_alembic()
        from alembic.config import Config

        config = Config()
        config.set_main_option(
            "script_location", str(self.directory).replace("%", "%%")
        )
        config.attributes["target_metadata"] = self.metadata
        return config


def run_alembic_environment() -> None:
    """Run a generated env.py using the connection supplied by Canon's CLI.

    Existing env.py files may instead implement Alembic's connection-sharing
    recipe and consume config.attributes['connection'] and ['target_metadata'].
    """
    from alembic import context

    connection = context.config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise RuntimeError("Run this environment with fastapi-canon db")
    context.configure(
        connection=connection,
        target_metadata=context.config.attributes["target_metadata"],
        # Generate portable batch directives even when developing on Postgres.
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _require_alembic() -> None:
    try:
        import alembic  # noqa: F401 - validate the optional runtime before side effects
    except ImportError as error:
        raise ImportError(
            "Install fastapi-canon[migrations] for Alembic support"
        ) from error


def _run_operation(
    connection: Connection, config: Config, operation: Callable[[Config], object]
) -> None:
    config.attributes["connection"] = connection
    operation(config)


def _sqlite_foreign_keys(connection: Connection, enabled: bool) -> None:
    # SQLite ignores PRAGMA foreign_keys inside a transaction. The adapted
    # DBAPI API avoids SQLAlchemy's autobegin before toggling enforcement.
    # The runner checks references before committing each batch rebuild.
    dbapi_connection = connection.connection.dbapi_connection
    if dbapi_connection is None:
        raise RuntimeError("SQLite migration connection is closed")
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(
            "PRAGMA foreign_keys=ON" if enabled else "PRAGMA foreign_keys=OFF"
        )
    finally:
        cursor.close()


def _require_schema(connection: Connection, metadata: MetaData) -> None:
    inspector = inspect(connection)
    missing = []
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name, schema=table.schema):
            missing.append(f"table {table.fullname}")
            continue
        actual = {
            column["name"]
            for column in inspector.get_columns(table.name, schema=table.schema)
        }
        missing.extend(
            f"column {table.fullname}.{column.name}"
            for column in table.columns
            if column.name not in actual
        )
    if missing:
        raise RuntimeError(
            "Database schema is incomplete: "
            + ", ".join(sorted(missing))
            + ". Add a forward repair migration; do not stamp or rewrite migration history."
        )
