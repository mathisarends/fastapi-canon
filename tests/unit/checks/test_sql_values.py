import json
from pathlib import Path

import pytest

from fastapi_canon.cli import main


@pytest.mark.parametrize(
    ("imports", "load"),
    [
        (
            "from sqlmodel.ext.asyncio.session import AsyncSession",
            "await session.get(Record, 1)",
        ),
        (
            "from sqlmodel import select\nfrom sqlmodel.ext.asyncio.session import AsyncSession",
            "(await session.exec(select(Record).limit(1).execution_options(populate_existing=True))).one_or_none()",
        ),
        (
            "from sqlalchemy import select\nfrom sqlalchemy.ext.asyncio import AsyncSession",
            "(await session.execute(select(Record).limit(1).execution_options(populate_existing=True))).scalar_one_or_none()",
        ),
    ],
)
@pytest.mark.parametrize("declared", [False, True])
def test_known_orm_entity_shapes_reach_domain_methods(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    imports: str,
    load: str,
    declared: bool,
) -> None:
    (tmp_path / "api.py").write_text(
        f"""
from sqlmodel import SQLModel, Field
from fastapi_canon import APIRouter, Error
{imports}
class Missing(Exception): pass
missing = Error(Missing,status=404,code='missing',title='Missing')
class Record(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    def validate_domain(self):
        if self.name.strip() == 'bad': raise Missing()
router=APIRouter()
@router.get('/', {"raises=[missing]" if declared else ""})
async def route(session: AsyncSession):
    row = {load}
    if row is not None: row.validate_domain()
""",
        encoding="utf8",
    )
    status = main(
        [
            "check",
            "errors",
            str(tmp_path),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert operation["complete"], operation["diagnostics"]
    assert [d["code"] for d in operation["diagnostics"]] == (
        [] if declared else ["CAN002"]
    )
    if not declared:
        assert operation["diagnostics"][0]["trace"][-1] == "api.Record.validate_domain"
    assert status == int(not declared)


@pytest.mark.parametrize(
    "boundary",
    ["entity", "statement", "converter", "converter_query", "mapped_converter"],
)
def test_unresolved_orm_shapes_and_custom_converters_remain_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], boundary: str
) -> None:
    column = (
        "Field(sa_type=CustomType)" if boundary.startswith("converter") else "Field()"
    )
    load = {
        "entity": "await session.get(entity, 1)",
        "statement": "(await session.execute(text('select name from records'))).scalar_one()",
        "converter": "await session.get(Record, 1)",
        "converter_query": "(await session.exec(statement)).one_or_none()",
        "mapped_converter": "await session.get(Record, 1)",
    }[boundary]
    record = f"""
class Record(SQLModel,table=True):
    id: int | None = Field(default=None,primary_key=True)
    name: str = {column}
    def validate_domain(self): pass
"""
    if boundary == "mapped_converter":
        record = """
class Base(DeclarativeBase): pass
class Record(Base):
    __tablename__ = 'records'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(CustomType())
    def validate_domain(self): pass
"""
    (tmp_path / "api.py").write_text(
        f"""
from typing import Any
from sqlalchemy import text, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator
from sqlmodel import SQLModel, Field, select
from sqlmodel.ext.asyncio.session import AsyncSession
from fastapi_canon import APIRouter
class Missing(Exception): pass
class CustomType(TypeDecorator):
    impl = String
    def process_result_value(self,value,dialect): raise Missing()
{record}
router=APIRouter()
statement = select(Record).limit(1)
@router.get('/')
async def route(session:AsyncSession,entity:Any):
    row = {load}
    if row is not None: row.validate_domain()
""",
        encoding="utf8",
    )
    status = main(
        [
            "check",
            "errors",
            str(tmp_path),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert not operation["complete"]
    assert status == 1
    if boundary in {"converter", "mapped_converter"}:
        assert any(
            d["message"] == "unresolved custom ORM value transformation"
            for d in operation["diagnostics"]
        )
