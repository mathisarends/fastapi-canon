import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import (
    AbstractContextManager,
    asynccontextmanager,
    contextmanager,
    nullcontext,
)
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import Column, MetaData, String, Table, event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from fastapi_canon import AlembicMigrations, Composition, Database, Feature


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("success", ["enter", "query", "exit", "dispose"]),
        ("operation", ["enter", "query", "exit", "dispose"]),
        ("cancel", ["enter", "query", "exit", "dispose"]),
        ("entry", ["enter", "exit", "dispose"]),
        ("factory", ["dispose"]),
        ("cleanup", ["enter", "query", "exit", "dispose"]),
    ],
)
async def test_engine_context_exits_before_disposal_even_on_failure(
    outcome: str, expected: list[str]
) -> None:
    calls: list[str] = []
    observed_errors: list[type[BaseException]] = []

    def query(*args: object) -> None:
        if args[2] == "SELECT 1":
            calls.append("query")

    @contextmanager
    def context(engine: AsyncEngine) -> Iterator[None]:
        calls.append("enter")
        event.listen(engine.sync_engine, "before_cursor_execute", query)
        try:
            if outcome == "entry":
                raise RuntimeError("entry failed")
            yield
        except BaseException as error:
            observed_errors.append(type(error))
            raise
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", query)
            calls.append("exit")
        if outcome == "cleanup":
            raise RuntimeError("cleanup failed")

    def factory(engine: AsyncEngine) -> AbstractContextManager[None]:
        def disposed(sync_engine: object) -> None:
            calls.append("dispose")

        event.listen(engine.sync_engine, "engine_disposed", disposed)
        if outcome == "factory":
            raise RuntimeError("factory failed")
        return context(engine)

    database = Database(url="sqlite://", engine_context=factory)
    expected_error = (
        pytest.raises(asyncio.CancelledError)
        if outcome == "cancel"
        else pytest.raises(RuntimeError, match="failed")
        if outcome != "success"
        else nullcontext()
    )
    async with asyncio.timeout(10):
        with expected_error:
            async with database.open_engine() as engine, engine.connect() as connection:
                assert await connection.scalar(text("SELECT 1")) == 1
                if outcome == "operation":
                    raise RuntimeError("operation failed")
                if outcome == "cancel":
                    raise asyncio.CancelledError()
    assert calls == expected
    assert observed_errors == (
        [asyncio.CancelledError]
        if outcome == "cancel"
        else [RuntimeError]
        if outcome in {"operation", "entry"}
        else []
    )


async def test_context_covers_migration_validation_and_failed_startup(
    tmp_path: Path,
) -> None:
    metadata = MetaData()
    Table("items", metadata, Column("name", String, primary_key=True))
    migrations = AlembicMigrations(directory=tmp_path / "migrations", metadata=metadata)
    migrations.initialize()
    url = f"sqlite:///{tmp_path / 'startup.db'}"
    async with asyncio.timeout(10), Database(url=url).open_engine() as engine:
        await migrations.revision(engine, message="initial", autogenerate=True)
    calls: list[str] = []

    @contextmanager
    def context(engine: AsyncEngine) -> Iterator[None]:
        def query(*args: object) -> None:
            calls.append("query")

        def disposed(sync_engine: object) -> None:
            calls.append("dispose")

        event.listen(engine.sync_engine, "engine_disposed", disposed)
        event.listen(engine.sync_engine, "before_cursor_execute", query)
        calls.append("enter")
        try:
            yield
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", query)
            calls.append("exit")

    database = Database(url=url, migrations=migrations, engine_context=context)
    app = Composition(features=[database.feature]).build(FastAPI())
    async with asyncio.timeout(10):
        with pytest.raises(RuntimeError, match="pending"):
            async with app.router.lifespan_context(app):
                pytest.fail("Startup must reject pending migrations")
    assert calls[0] == "enter"
    assert "query" in calls
    assert calls[-2:] == ["exit", "dispose"]


@pytest.mark.parametrize("fail_startup", [False, True])
async def test_context_is_per_app_and_outlives_feature_cleanup(
    fail_startup: bool,
) -> None:
    calls: list[str] = []
    engines: list[AsyncEngine] = []

    @contextmanager
    def context(engine: AsyncEngine) -> Iterator[None]:
        engines.append(engine)
        calls.append("enter")
        try:
            yield
        finally:
            calls.append("exit")

    database = Database(url="sqlite://", engine_context=context)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = await app.state.dishka_container.get(AsyncEngine)
        try:
            if fail_startup:
                raise RuntimeError("feature startup failed")
            yield
        finally:
            async with engine.connect() as connection:
                assert await connection.scalar(text("SELECT 1")) == 1
            calls.append("feature-exit")

    feature = Feature(name="consumer", lifespan=lifespan)
    first = Composition(features=[database.feature, feature]).build(FastAPI())
    second = Composition(features=[database.feature, feature]).build(FastAPI())
    assert calls == []
    async with asyncio.timeout(10):
        with (
            pytest.raises(RuntimeError, match="feature startup failed")
            if fail_startup
            else nullcontext()
        ):
            async with first.router.lifespan_context(first):
                assert calls == ["enter"]
        with (
            pytest.raises(RuntimeError, match="feature startup failed")
            if fail_startup
            else nullcontext()
        ):
            async with second.router.lifespan_context(second):
                assert calls == ["enter", "feature-exit", "exit", "enter"]
    assert len(engines) == 2
    assert engines[0] is not engines[1]
    assert calls == ["enter", "feature-exit", "exit"] * 2


async def test_disabled_startup_check_preserves_lazy_connections_and_explicit_commands(
    tmp_path: Path,
) -> None:
    metadata = MetaData()
    Table("items", metadata, Column("name", String, primary_key=True))
    migrations = AlembicMigrations(directory=tmp_path / "migrations", metadata=metadata)
    migrations.initialize()
    filename = tmp_path / "lazy.db"
    calls: list[str] = []

    @contextmanager
    def context(engine: AsyncEngine) -> Iterator[None]:
        calls.append("enter")
        try:
            yield
        finally:
            calls.append("exit")

    database = Database(
        url=f"sqlite:///{filename}",
        migrations=migrations,
        check_migrations_on_startup=False,
        engine_context=context,
    )
    app = Composition(features=[database.feature]).build(FastAPI())
    async with asyncio.timeout(15), app.router.lifespan_context(app):
        assert calls == []
        assert not filename.exists()
        assert database.migrations is migrations
        async with database.open_engine() as engine:
            assert calls == ["enter"]
            assert not filename.exists()
            await migrations.revision(engine, message="initial", autogenerate=True)
            with pytest.raises(RuntimeError, match="pending"):
                await migrations.require_current(engine)
            await migrations.upgrade(engine)
            await migrations.require_current(engine)
            await migrations.check(engine)
    assert calls == ["enter", "exit"]
    assert filename.is_file()
