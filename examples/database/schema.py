from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DatabaseSchema(DeclarativeBase):
    pass


class ItemRow(DatabaseSchema):
    __tablename__ = "items"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
