import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import MetaData, String
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fastapi_canon import Composition, Feature
from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integrations.database import AlembicMigrations, Database, Tables


class Schema(DeclarativeBase):
    pass


class Item(Schema):
    __tablename__ = "items"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)


class Note(Schema):
    __tablename__ = "notes"

    text: Mapped[str] = mapped_column(String(80), primary_key=True)


class Conventional(DeclarativeBase):
    metadata = MetaData(naming_convention={"pk": "pk_%(table_name)s"})


class Tag(Conventional):
    __tablename__ = "tags"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)


async def test_startup_requires_the_tables_of_every_installed_feature(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path / 'tables.db'}"
    items_only = MetaData()
    Schema.metadata.tables["items"].to_metadata(items_only)
    applied = AlembicMigrations(directory=tmp_path / "migrations", metadata=items_only)
    applied.initialize()
    async with asyncio.timeout(20), Database(url=url).open_engine() as engine:
        await applied.revision(engine, message="items", autogenerate=True)
        await applied.upgrade(engine)
    database = Database(
        url=url, migrations=AlembicMigrations(directory=tmp_path / "migrations")
    )
    engines: list[AsyncEngine] = []

    @asynccontextmanager
    async def use_engine(app: FastAPI) -> AsyncGenerator[None]:
        engines.append(await app.state.dishka_container.get(AsyncEngine))
        yield

    items = Feature(name="items", integrations=[Tables(Item)])
    notes = Feature(
        name="notes", lifespan=use_engine, integrations=[Tables(Note.__table__)]
    )

    ready = Composition(features=[items], integrations=[database]).build(FastAPI())
    async with asyncio.timeout(10), ready.router.lifespan_context(ready):
        assert ready.state.dishka_container is not None
    incomplete = Composition(features=[items, notes], integrations=[database]).build(
        FastAPI()
    )
    async with asyncio.timeout(10):
        with pytest.raises(RuntimeError, match="schema is incomplete"):
            async with incomplete.router.lifespan_context(incomplete):
                pytest.fail("Startup must require the notes table")
    assert engines == []


@pytest.mark.parametrize(
    ("metadata", "second", "message"),
    [
        (None, Tables(Item), "'items' and 'copies'"),
        (Schema.metadata, Tables(Note), "declares metadata="),
        (None, Tables(Tag), "naming conventions"),
    ],
    ids=["duplicate-table", "explicit-metadata", "naming-conventions"],
)
def test_conflicting_table_declarations_fail_the_composition_check(
    tmp_path: Path, metadata: MetaData | None, second: Tables, message: str
) -> None:
    database = Database(
        url="sqlite://",
        migrations=AlembicMigrations(directory=tmp_path, metadata=metadata),
    )
    composition = Composition(
        features=[
            Feature(name="items", integrations=[Tables(Item)]),
            Feature(name="copies", integrations=[second]),
        ],
        integrations=[database],
    )

    report = composition.check(FastAPI())

    assert [issue.code for issue in report.issues] == ["canon.database.E001"]
    assert message in report.issues[0].message


def test_tables_accept_only_mapped_classes_and_tables() -> None:
    with pytest.raises(FeatureConfigurationError, match="mapped classes or Table"):
        Tables(Schema)
