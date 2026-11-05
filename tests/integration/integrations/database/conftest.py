import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import URL, Column, MetaData, String, Table, text
from sqlalchemy.ext.asyncio import create_async_engine

from fastapi_canon import Database


@pytest.fixture(params=["sqlite", "postgres"])
async def database(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[Database]:
    if request.param == "sqlite":
        yield Database(url=URL.create("sqlite", database=str(tmp_path / "test.db")))
        return
    url = os.environ.get("CANON_TEST_POSTGRES_URL")
    if url is None:
        pytest.skip("Set CANON_TEST_POSTGRES_URL to run real PostgreSQL contracts")
    schema = "canon_" + uuid4().hex
    engine = create_async_engine(url)
    async with asyncio.timeout(15), engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield Database(
            url=url,
            engine_options={
                "connect_args": {"server_settings": {"search_path": schema}}
            },
        )
    finally:
        async with asyncio.timeout(15), engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


@pytest.fixture
def metadata() -> MetaData:
    result = MetaData()
    Table(
        "items",
        result,
        Column("name", String(80), primary_key=True),
    )
    return result


@pytest.fixture
async def items(database: Database, metadata: MetaData) -> Table:
    async with (
        asyncio.timeout(15),
        database.open_engine() as engine,
        engine.begin() as connection,
    ):
        await connection.run_sync(metadata.create_all)
    return metadata.tables["items"]
