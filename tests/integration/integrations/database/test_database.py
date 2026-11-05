import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from dishka import AsyncContainer, Provider, Scope, provide
from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Table, event, insert, select, text
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import select as model_select
from sqlmodel.ext.asyncio.session import AsyncSession as ModelSession
from starlette.types import Message, Receive, Send
from starlette.types import Scope as ASGIScope

from fastapi_canon import (
    Composition,
    Database,
    Error,
    ErrorRegistry,
    Feature,
    Scoped,
    ServiceScope,
)


@pytest.mark.parametrize(
    ("outcome", "status", "persisted"),
    [
        ("success", 200, True),
        ("handled", 409, False),
        ("failure", 500, False),
        ("commit-failure", 500, False),
        ("invalid-response", 500, False),
    ],
)
async def test_http_scope_finishes_before_response_start(
    database: Database, items: Table, outcome: str, status: int, persisted: bool
) -> None:
    class Rejected(Exception):
        pass

    router = APIRouter(route_class=DishkaRoute)
    sessions: list[AsyncSession] = []

    @router.post("/items", response_model=list[str])
    async def create(session: FromDishka[AsyncSession]) -> Any:
        assert await session.scalar(text("SELECT 1")) == 1
        sessions.append(session)
        await session.execute(insert(items).values(name=outcome))
        if outcome == "handled":
            raise Rejected()
        if outcome == "failure":
            raise RuntimeError("application failed")
        if outcome == "commit-failure":

            def reject_commit(session: object) -> None:
                raise RuntimeError("commit failed")

            event.listen(
                session.sync_session, "before_commit", reject_commit, once=True
            )
        if outcome == "invalid-response":
            return {"wrong": "shape"}
        return [outcome]

    app = Composition(
        features=[
            database.feature,
            Feature(
                name="items",
                routers=[router],
                errors=ErrorRegistry(
                    type_base="urn:test:problem",
                    errors=[
                        Error(
                            Rejected,
                            type="urn:test:rejected",
                            code="rejected",
                            title="Rejected",
                            status=409,
                        )
                    ],
                ),
            ),
        ],
    ).apply(FastAPI())
    at_response_start: list[list[str]] = []
    checked_out = 0
    connections_at_start: list[int] = []

    async def observed(scope: ASGIScope, receive: Receive, send: Send) -> None:
        async def observe(message: Message) -> None:
            if message["type"] == "http.response.start":
                connections_at_start.append(checked_out)
                async with (
                    database.open_engine() as engine,
                    engine.connect() as connection,
                ):
                    at_response_start.append(
                        list((await connection.scalars(select(items.c.name))).all())
                    )
            await send(message)

        await app(scope, receive, observe)

    async with asyncio.timeout(15), app.router.lifespan_context(app):
        engine = await app.state.dishka_container.get(AsyncEngine)

        def checkout(*args: object) -> None:
            nonlocal checked_out
            checked_out += 1

        def checkin(*args: object) -> None:
            nonlocal checked_out
            checked_out -= 1

        event.listen(engine.sync_engine, "checkout", checkout)
        event.listen(engine.sync_engine, "checkin", checkin)
        async with AsyncClient(
            transport=ASGITransport(observed, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post("/items")
    assert response.status_code == status
    assert at_response_start == ([[outcome]] if persisted else [[]])
    assert connections_at_start == [0]
    assert len(sessions) == 1
    with pytest.raises(InvalidRequestError, match="permanently closed"):
        await sessions[0].execute(text("SELECT 1"))


async def test_existing_and_future_app_routes_share_the_early_scope(
    database: Database, items: Table
) -> None:
    calls: list[str] = []

    async def prerequisite() -> None:
        calls.append("dependency")

    app = FastAPI(dependencies=[Depends(prerequisite, use_cache=False)])

    @app.post("/before")
    @inject
    async def before(session: FromDishka[AsyncSession]) -> str:
        await session.execute(insert(items).values(name="before"))
        return "before"

    Composition(features=[database.feature]).apply(app)

    @app.post("/after")
    @inject
    async def after(session: FromDishka[AsyncSession]) -> str:
        await session.execute(insert(items).values(name="after"))
        return "after"

    async with asyncio.timeout(15), app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.post("/before")).json() == "before"
            assert (await client.post("/after")).json() == "after"
        async with database.open_engine() as engine, engine.connect() as connection:
            rows = list(
                (
                    await connection.scalars(
                        select(items.c.name).order_by(items.c.name)
                    )
                ).all()
            )
    assert rows == ["after", "before"]
    assert calls == ["dependency", "dependency"]


async def test_cancellation_rolls_back_the_request(
    database: Database, items: Table
) -> None:
    router = APIRouter(route_class=DishkaRoute)

    @router.post("/cancel")
    async def cancel(session: FromDishka[AsyncSession]) -> None:
        await session.execute(insert(items).values(name="cancelled"))
        raise asyncio.CancelledError()

    app = Composition(
        features=[database.feature, Feature(name="cancel", routers=[router])]
    ).apply(FastAPI())
    async with asyncio.timeout(15), app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://test"
        ) as client:
            with pytest.raises(asyncio.CancelledError):
                await client.post("/cancel")
        async with database.open_engine() as engine, engine.connect() as connection:
            assert list((await connection.scalars(select(items.c.name))).all()) == []


async def test_stream_starts_after_the_handler_session_is_closed(
    database: Database, items: Table
) -> None:
    router = APIRouter(route_class=DishkaRoute)

    @router.get("/stream")
    async def stream(session: FromDishka[AsyncSession]) -> StreamingResponse:
        await session.execute(insert(items).values(name="committed"))

        async def chunks() -> AsyncIterator[str]:
            with pytest.raises(InvalidRequestError, match="permanently closed"):
                await session.execute(text("SELECT 1"))
            async with database.open_engine() as engine, engine.connect() as connection:
                yield str(await connection.scalar(select(items.c.name)))

        return StreamingResponse(chunks())

    app = Composition(
        features=[database.feature, Feature(name="stream", routers=[router])]
    ).apply(FastAPI())
    async with (
        asyncio.timeout(15),
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
    ):
        response = await client.get("/stream")
    assert response.status_code == 200
    assert response.text == "committed"


async def test_sqlmodel_and_sqlalchemy_inject_the_same_session(tmp_path: Path) -> None:
    database = Database(
        url=f"sqlite:///{tmp_path / 'model.db'}", session_class=ModelSession
    )
    app = Composition(features=[database.feature]).apply(FastAPI())
    async with (
        asyncio.timeout(10),
        app.router.lifespan_context(app),
        app.state.dishka_container(scope=Scope.REQUEST) as scope,
    ):
        session = await scope.get(ModelSession)
        assert await scope.get(AsyncSession) is session
        assert (await session.exec(model_select(1))).one() == 1
        factory = await scope.get(async_sessionmaker[ModelSession])
        assert await scope.get(async_sessionmaker[AsyncSession]) is factory
        async with factory() as standalone:
            assert isinstance(standalone, ModelSession)
            assert not standalone.sync_session.expire_on_commit
            assert (await standalone.exec(model_select(2))).one() == 2


async def test_foreign_keys_are_enforced_on_sqlite(tmp_path: Path) -> None:
    database = Database(url=f"sqlite:///{tmp_path / 'foreign.db'}")
    async with asyncio.timeout(10), database.open_engine() as engine:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            )
            await connection.execute(
                text("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
            )
        async with engine.begin() as connection:
            assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
            with pytest.raises(IntegrityError):
                await connection.execute(text("INSERT INTO child VALUES (123)"))


async def test_service_scopes_use_fresh_sessions_and_commit_before_returning(
    database: Database, items: Table
) -> None:
    class Writer:
        def __init__(self, session: AsyncSession) -> None:
            self.session = session

        async def write(self, name: str) -> None:
            await self.session.execute(insert(items).values(name=name))

    class Services(Provider):
        writer = provide(Writer, scope=Scope.REQUEST)

        @provide(scope=Scope.APP)
        def writer_scope(self, container: AsyncContainer) -> ServiceScope[Writer]:
            return ServiceScope(container, Writer)

    app = Composition(
        features=[database.feature, Feature(name="services", providers=[Services])]
    ).apply(FastAPI())
    sessions: list[AsyncSession] = []
    async with asyncio.timeout(15), app.router.lifespan_context(app):
        scope_factory = await app.state.dishka_container.get(ServiceScope[Writer])

        async def write(name: str) -> None:
            async with scope_factory() as writer:
                sessions.append(writer.session)
                await writer.write(name)

        await asyncio.gather(write("one"), write("two"))
        with pytest.raises(ValueError, match="rollback"):
            async with scope_factory() as writer:
                await writer.write("discarded")
                raise ValueError("rollback")
        async with database.open_engine() as engine, engine.connect() as connection:
            assert list(
                (
                    await connection.scalars(
                        select(items.c.name).order_by(items.c.name)
                    )
                ).all()
            ) == ["one", "two"]
    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]


@pytest.mark.parametrize("outcome", ["success", "error", "cancel", "commit-failure"])
async def test_scoped_use_case_finishes_transaction_before_return(
    database: Database, items: Table, outcome: str
) -> None:
    started = asyncio.Event()
    sessions: list[AsyncSession] = []

    class Write:
        def __init__(self, session: AsyncSession) -> None:
            self.session = session

        async def __call__(self, name: str) -> str:
            sessions.append(self.session)
            await self.session.execute(insert(items).values(name=name))
            started.set()
            if outcome == "error":
                raise ValueError("write failed")
            if outcome == "cancel":
                await asyncio.Event().wait()
            if outcome == "commit-failure":

                def reject_commit(session: object) -> None:
                    raise ValueError("commit failed")

                event.listen(self.session.sync_session, "before_commit", reject_commit)
            return name

    class Services(Provider):
        write = provide(Write, scope=Scope.REQUEST)

        @provide(scope=Scope.APP)
        def scoped_write(self, container: AsyncContainer) -> Scoped[Write]:
            return Scoped(container, Write)

    app = Composition(
        features=[database.feature, Feature(name="writers", providers=[Services])]
    ).apply(FastAPI())
    async with asyncio.timeout(15), app.router.lifespan_context(app):
        write = await app.state.dishka_container.get(Scoped[Write])
        task = asyncio.create_task(write(outcome))
        await started.wait()
        if outcome == "success":
            assert await task == outcome
        elif outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome == "commit-failure":
            with pytest.raises(ExceptionGroup) as failure:
                await task
            assert failure.value.subgroup(ValueError) is not None
        else:
            with pytest.raises(ValueError, match="failed"):
                await task
        async with database.open_engine() as engine, engine.connect() as connection:
            names = list((await connection.scalars(select(items.c.name))).all())
        assert names == ([outcome] if outcome == "success" else [])
        assert len(sessions) == 1
        with pytest.raises(InvalidRequestError, match="permanently closed"):
            await sessions[0].execute(text("SELECT 1"))


async def test_explicit_commit_survives_a_later_http_rejection(
    database: Database, items: Table
) -> None:
    router = APIRouter(route_class=DishkaRoute)

    @router.post("/reject")
    async def reject(session: FromDishka[AsyncSession]) -> None:
        await session.execute(insert(items).values(name="durable"))
        await session.commit()
        await session.execute(insert(items).values(name="transient"))
        raise HTTPException(status_code=401)

    app = Composition(
        features=[database.feature, Feature(name="reject", routers=[router])]
    ).apply(FastAPI())
    async with asyncio.timeout(15), app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.post("/reject")).status_code == 401
        async with database.open_engine() as engine, engine.connect() as connection:
            assert list((await connection.scalars(select(items.c.name))).all()) == [
                "durable"
            ]


async def test_savepoint_failure_preserves_the_outer_transaction(
    database: Database, items: Table
) -> None:
    app = Composition(features=[database.feature]).apply(FastAPI())
    async with asyncio.timeout(15), app.router.lifespan_context(app):
        async with app.state.dishka_container(scope=Scope.REQUEST) as scope:
            session = await scope.get(AsyncSession)
            await session.execute(insert(items).values(name="before"))
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(insert(items).values(name="before"))
            await session.execute(insert(items).values(name="after"))
        async with database.open_engine() as engine, engine.connect() as connection:
            assert list(
                (
                    await connection.scalars(
                        select(items.c.name).order_by(items.c.name)
                    )
                ).all()
            ) == ["after", "before"]


async def test_native_upserts_and_backend_row_locks_remain_available(
    database: Database, items: Table
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    app = Composition(features=[database.feature]).apply(FastAPI())
    async with asyncio.timeout(15), app.router.lifespan_context(app):
        engine = await app.state.dishka_container.get(AsyncEngine)
        postgres = engine.dialect.name == "postgresql"
        dialect_insert = pg_insert if postgres else sqlite_insert
        async with app.state.dishka_container(scope=Scope.REQUEST) as scope:
            session = await scope.get(AsyncSession)
            statement = (
                dialect_insert(items)
                .values(name="one")
                .on_conflict_do_nothing()
                .returning(items.c.name)
            )
            assert (await session.execute(statement)).scalar_one() == "one"
            assert (await session.execute(statement)).scalar_one_or_none() is None
        async with app.state.dishka_container(scope=Scope.REQUEST) as scope:
            session = await scope.get(AsyncSession)
            assert await session.scalar(select(items.c.name).with_for_update()) == "one"
            async with (
                database.open_engine() as observer,
                observer.connect() as connection,
            ):
                rows = list(
                    (
                        await connection.scalars(
                            select(items.c.name).with_for_update(skip_locked=True)
                        )
                    ).all()
                )
            assert rows == ([] if postgres else ["one"])
