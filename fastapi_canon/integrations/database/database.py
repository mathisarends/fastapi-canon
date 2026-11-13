"""App-owned engines and request-owned SQLAlchemy sessions."""

from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AbstractContextManager, asynccontextmanager, nullcontext
from typing import Any, Literal

from anyio import CancelScope
from dishka import Provider, Scope, provide
from fastapi import FastAPI
from sqlalchemy import URL, Connection, event, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fastapi_canon.feature import Feature
from fastapi_canon.integrations.database.locks import TransactionLocks
from fastapi_canon.integrations.database.migrations import AlembicMigrations


class Database:
    """A reusable database declaration; each app owns its own engine and pool.

    Bare sqlite/postgresql URLs select aiosqlite/asyncpg. Explicit drivers must
    match those supported async drivers. Settings and credentials stay app-owned.
    """

    def __init__(
        self,
        *,
        url: str | URL,
        name: str = "database",
        session_class: type[AsyncSession] = AsyncSession,
        engine_options: Mapping[str, Any] | None = None,
        migrations: AlembicMigrations | None = None,
        check_migrations_on_startup: bool = True,
        engine_context: Callable[[AsyncEngine], AbstractContextManager[object]]
        | None = None,
        sqlite_advisory_locks: Literal["error", "ignore"] = "error",
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
        self._engine_options = dict(engine_options or {})
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
        self._feature = Feature(
            name=name,
            providers=[
                lambda: _DatabaseProvider(self, session_class, sqlite_advisory_locks)
            ],
            lifespan=self._lifespan,
        )

    @property
    def feature(self) -> Feature:
        """The immutable contribution to pass to Composition."""
        return self._feature

    @property
    def migrations(self) -> AlembicMigrations | None:
        return self._migrations

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

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI) -> AsyncGenerator[None]:
        if self._migrations is not None and self._check_migrations_on_startup:
            engine = await app.state.dishka_container.get(AsyncEngine)
            await self._migrations.require_current(engine)
        yield


class _DatabaseProvider(Provider):
    scope = Scope.APP

    def __init__(
        self,
        database: Database,
        session_class: type[AsyncSession],
        sqlite_locks: Literal["error", "ignore"],
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
    def transaction_locks(self, session: AsyncSession) -> TransactionLocks:
        return TransactionLocks(session, sqlite=self._sqlite_locks)

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
