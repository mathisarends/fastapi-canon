"""Optional SQLAlchemy database ownership and Alembic migrations."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .database import Database
    from .migrations import AlembicMigrations, run_alembic_environment

__all__ = ["AlembicMigrations", "Database", "run_alembic_environment"]


def __getattr__(name: str) -> Any:
    if name == "Database":
        return import_module(f"{__name__}.database").Database
    if name in {"AlembicMigrations", "run_alembic_environment"}:
        return getattr(import_module(f"{__name__}.migrations"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
