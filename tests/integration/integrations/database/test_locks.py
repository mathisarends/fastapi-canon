import asyncio
from contextlib import nullcontext
from pathlib import Path

import pytest
from dishka import Scope
from fastapi import FastAPI
from sqlalchemy import text

from fastapi_canon import Composition, Database, TransactionLocks


@pytest.mark.parametrize("outcome", ["commit", "rollback", "cancel"])
async def test_postgres_advisory_lock_follows_its_scoped_transaction(
    database: Database, outcome: str
) -> None:
    class Abort(Exception):
        pass

    async with asyncio.timeout(15), database.open_engine() as observer_engine:
        if observer_engine.dialect.name != "postgresql":
            pytest.skip("Cross-process advisory lock semantics require PostgreSQL")
        app = Composition(features=[database.feature]).apply(FastAPI())
        async with (
            app.router.lifespan_context(app),
            observer_engine.begin() as observer,
        ):
            expected = (
                pytest.raises(Abort)
                if outcome == "rollback"
                else pytest.raises(asyncio.CancelledError)
                if outcome == "cancel"
                else nullcontext()
            )
            with expected:
                async with app.state.dishka_container(scope=Scope.REQUEST) as scope:
                    locks = await scope.get(TransactionLocks)
                    await locks.acquire(-1234567890123)
                    assert (
                        await observer.scalar(
                            text("SELECT pg_try_advisory_xact_lock(-1234567890123)")
                        )
                        is False
                    )
                    if outcome == "rollback":
                        raise Abort()
                    if outcome == "cancel":
                        raise asyncio.CancelledError()
            assert (
                await observer.scalar(
                    text("SELECT pg_try_advisory_xact_lock(-1234567890123)")
                )
                is True
            )


async def test_sqlite_advisory_lock_omission_requires_explicit_configuration(
    tmp_path: Path,
) -> None:
    database = Database(url=f"sqlite:///{tmp_path / 'locks.db'}")
    app = Composition(features=[database.feature]).apply(FastAPI())
    async with (
        asyncio.timeout(10),
        app.router.lifespan_context(app),
        app.state.dishka_container(scope=Scope.REQUEST) as scope,
    ):
        locks = await scope.get(TransactionLocks)
        with pytest.raises(NotImplementedError, match="require PostgreSQL"):
            await locks.acquire(123)
    explicit = Database(
        url=f"sqlite:///{tmp_path / 'locks.db'}", sqlite_advisory_locks="ignore"
    )
    permitted = Composition(features=[explicit.feature]).apply(FastAPI())
    async with (
        asyncio.timeout(10),
        permitted.router.lifespan_context(permitted),
        permitted.state.dishka_container(scope=Scope.REQUEST) as scope,
    ):
        locks = await scope.get(TransactionLocks)
        assert await locks.acquire(123) is None
