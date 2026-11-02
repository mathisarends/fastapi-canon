from sqlalchemy import MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ItemRow(Base):
    __tablename__ = "items"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)


metadata: MetaData = Base.metadata
