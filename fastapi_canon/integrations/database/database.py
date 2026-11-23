from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    nullcontext,
)
from dataclasses import dataclass
from functools import partial
from typing import Any, ClassVar

from anyio import CancelScope
from dishka import AsyncContainer, Provider, Scope, provide
from fastapi import FastAPI
from sqlalchemy import (
    URL,
    Connection,
    ForeignKeyConstraint,
    MetaData,
    Table,
    event,
    inspect,
    make_url,
)
from sqlalchemy.exc import NoReferencedTableError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integration import (
    Installer,
    Integration,
    IntegrationContext,
    IntegrationError,
    Parts,
    ProviderContext,
)
from fastapi_canon.integrations.database.locks import (
    AcquireTransactionLock,
    SqliteLockPolicy,
)
from fastapi_canon.integrations.database.migrations import AlembicMigrations


class Database(Integration):
    """A reusable database declaration; each app owns its own engine and pool.

    Enable it with ``Composition(integrations=[database])``. Features contribute
    their tables with ``Tables``. Bare sqlite/postgresql URLs select
    aiosqlite/asyncpg. Explicit drivers must match those supported async drivers.
    Settings and credentials stay app-owned.
    """

    def __init__(
        self,
        *,
        url: str | URL,
        session_class: type[AsyncSession] = AsyncSession,
        engine_options: Mapping[str, Any] | None = None,
        migrations: AlembicMigrations | None = None,
        check_migrations_on_startup: bool = True,
        engine_context: Callable[[AsyncEngine], AbstractContextManager[object]]
        | None = None,
        sqlite_advisory_locks: SqliteLockPolicy = "error",
    ) -> None:
        normalized = make_url(url)
        drivers = {
            "sqlite": "sqlite+aiosqlite",
            "postgresql": "postgresql+asyncpg",
            "postgres": "postgresql+asyncpg",
            "postgresql+psycopg": "postgresql+asyncpg",
            "postgresql+psycopg2": "postgresql+asyncpg",
            "sqlite+pysqlite": "sqlite+aiosqlite",
        }
        normalized = normalized.set(
            drivername=drivers.get(normalized.drivername, normalized.drivername)
        )
        if normalized.drivername not in {"sqlite+aiosqlite", "postgresql+asyncpg"}:
            raise ValueError(
                "Database supports sqlite+aiosqlite and postgresql+asyncpg"
            )
        if not issubclass(session_class, AsyncSession):
            raise TypeError("session_class must inherit SQLAlchemy AsyncSession")
        self._url = normalized
        self._engine_options = dict(
            engine_options if engine_options is not None else {}
        )
        if (
            normalized.get_backend_name() == "sqlite"
            and "isolation_level" in self._engine_options
        ):
            raise ValueError(
                "Database manages SQLite transaction control; omit isolation_level"
            )
        self._migrations = migrations
        self._check_migrations_on_startup = check_migrations_on_startup
        self._engine_context = engine_context
        if sqlite_advisory_locks not in {"error", "ignore"}:
            raise ValueError("sqlite_advisory_locks must be 'error' or 'ignore'")
        self._session_class = session_class
        self._sqlite_advisory_locks = sqlite_advisory_locks

    @property
    def migrations(self) -> AlembicMigrations | None:
        return self._migrations

    def providers(self, context: ProviderContext) -> Sequence[Provider]:
        """Provide the engine, sessions and locks."""
        return (
            _DatabaseProvider(self, self._session_class, self._sqlite_advisory_locks),
        )

    def prepare(self, context: IntegrationContext) -> Installer | None:
        """Compose the feature tables and return the startup revision check."""
        migrations = _composed_migrations(self._migrations, context.parts)
        return Installer(partial(self._install, migrations), before_features=True)

    @asynccontextmanager
    async def open_engine(self) -> AsyncGenerator[AsyncEngine]:
        """Own an engine and its optional context, shared by the app and CLI.

        The context starts before the first use and exits before pool disposal,
        including when context entry or application startup fails. Its yielded
        value is ignored; the database retains ownership of the original engine.
        """
        engine = create_async_engine(self._url, **self._engine_options)
        try:
            if engine.dialect.name == "sqlite":
                event.listen(engine.sync_engine, "connect", _configure_sqlite)
                event.listen(engine.sync_engine, "begin", _begin_sqlite)
            with (
                self._engine_context(engine)
                if self._engine_context is not None
                else nullcontext()
            ):
                yield engine
        finally:
            with CancelScope(shield=True):
                await engine.dispose()

    def _install(
        self,
        migrations: AlembicMigrations | None,
        app: FastAPI,
        container: AsyncContainer | None,
    ) -> Callable[[FastAPI], AbstractAsyncContextManager[None]] | None:
        setattr(app.state, _INSTALLED_KEY, InstalledDatabase(self, migrations))
        if (
            migrations is None
            or container is None
            or not self._check_migrations_on_startup
        ):
            return None

        @asynccontextmanager
        async def check_revisions(app: FastAPI) -> AsyncGenerator[None]:
            # Resolve through the container so an overridden engine is validated.
            engine = await container.get(AsyncEngine)
            await migrations.require_current(engine)
            yield

        return check_revisions


@dataclass(frozen=True, slots=True, init=False)
class Tables:
    """The mapped classes or tables one feature contributes to the ``Database``.

    Pass it to ``Feature(integrations=[Tables(Model, ...)])``. Migrations then
    use the tables of every installed feature as their metadata.
    """

    integration: ClassVar[type[Integration]] = Database
    tables: tuple[Table, ...]

    def __init__(self, model: object, /, *more_models: object) -> None:
        tables = tuple(_table(item) for item in (model, *more_models))
        if len({table.key for table in tables}) != len(tables):
            raise FeatureConfigurationError("Tables must name each table once")
        object.__setattr__(self, "tables", tables)


@dataclass(frozen=True, slots=True)
class InstalledDatabase:
    """A database and the migrations composed for one application."""

    database: Database
    migrations: AlembicMigrations | None


def installed_database(app: FastAPI) -> InstalledDatabase | None:
    """Return the database a Composition installed into *app*, if any."""
    installed = getattr(app.state, _INSTALLED_KEY, None)
    return installed if isinstance(installed, InstalledDatabase) else None


class _DatabaseProvider(Provider):
    scope = Scope.APP

    def __init__(
        self,
        database: Database,
        session_class: type[AsyncSession],
        sqlite_locks: SqliteLockPolicy,
    ) -> None:
        super().__init__()
        self._database = database
        self._session_class = session_class
        self._sqlite_locks = sqlite_locks
        self.provide(self._session, provides=session_class, scope=Scope.REQUEST)
        if session_class is not AsyncSession:
            self.alias(session_class, provides=AsyncSession)
            # The configured class is a runtime Dishka key, not a static type alias.
            self.alias(
                async_sessionmaker[AsyncSession],
                provides=async_sessionmaker[session_class],  # type: ignore[valid-type]  # The runtime session class forms a Dishka key.
            )

    @provide
    async def engine(self) -> AsyncGenerator[AsyncEngine]:
        async with self._database.open_engine() as engine:
            yield engine

    @provide
    def session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(
            engine,
            class_=self._session_class,
            expire_on_commit=False,
            close_resets_only=False,
        )

    @provide(scope=Scope.REQUEST)
    def transaction_locks(self, session: AsyncSession) -> AcquireTransactionLock:
        return AcquireTransactionLock(session, sqlite=self._sqlite_locks)

    async def _session(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> AsyncGenerator[AsyncSession, BaseException | None]:
        session = factory()
        try:
            exception = yield session
            if exception is None:
                await session.commit()
            else:
                with CancelScope(shield=True):
                    await session.rollback()
        except BaseException:
            with CancelScope(shield=True):
                await session.rollback()
            raise
        finally:
            with CancelScope(shield=True):
                await session.close()


_INSTALLED_KEY = "_fastapi_canon_database"


def _composed_migrations(
    migrations: AlembicMigrations | None, parts: Parts[Tables]
) -> AlembicMigrations | None:
    """Return *migrations* with the metadata that the feature tables form."""
    if migrations is None or not parts:
        return migrations
    if migrations.metadata is not None:
        raise IntegrationError(
            "canon.database.E001",
            "AlembicMigrations declares metadata=, but features "
            f"{', '.join(repr(feature) for feature, _ in parts)} contribute Tables",
            "Declare tables in one place: remove metadata= or the feature Tables.",
        )
    conventions: list[Mapping[str, Any]] = []
    for _, part in parts:
        for table in part.tables:
            if table.metadata.naming_convention not in conventions:
                conventions.append(table.metadata.naming_convention)
    if len(conventions) > 1:
        raise IntegrationError(
            "canon.database.E001",
            "Feature Tables use different SQLAlchemy naming conventions",
            "Give every contributed model the same MetaData naming_convention.",
        )
    metadata = MetaData(naming_convention=conventions[0])
    owners: dict[str, str] = {}
    for feature, part in parts:
        for table in part.tables:
            if table.key in owners:
                raise IntegrationError(
                    "canon.database.E001",
                    f"Table {table.key!r} is contributed by features "
                    f"{owners[table.key]!r} and {feature!r}",
                    "Keep each table in the Tables of exactly one feature.",
                )
            owners[table.key] = feature
            table.to_metadata(
                metadata,
                referred_schema_fn=lambda _table, _schema, constraint, declared: (
                    _referred_schema(constraint, declared)
                ),
            )
    return migrations.with_metadata(metadata)


def _referred_schema(
    constraint: ForeignKeyConstraint, declared: str | None
) -> str | None:
    """Keep the target a reference resolved to in its source ``MetaData``.

    An unqualified reference otherwise loses that metadata's default schema.
    """
    try:
        return constraint.referred_table.schema
    except NoReferencedTableError:
        return declared


def _table(model: object) -> Table:
    if isinstance(model, Table):
        return model
    table = getattr(inspect(model, raiseerr=False), "local_table", None)
    if not isinstance(table, Table):
        raise FeatureConfigurationError(
            f"Tables expects mapped classes or Table objects, got {model!r}"
        )
    return table


def _configure_sqlite(connection: Any, record: object) -> None:
    del record
    # Use explicit BEGIN on every supported Python/aiosqlite version instead
    # of sqlite3's legacy transaction mode, including DDL and SAVEPOINTs.
    connection.isolation_level = None
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _begin_sqlite(connection: Connection) -> None:
    connection.exec_driver_sql("BEGIN")
