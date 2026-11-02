import os
from pathlib import Path

from examples.database.schema import metadata
from fastapi_canon import AlembicMigrations, Database

database = Database(
    url=os.environ.get("CANON_EXAMPLE_DATABASE_URL", "sqlite:///./example.sqlite3"),
    migrations=AlembicMigrations(
        directory=Path(__file__).resolve().parent / "migrations", metadata=metadata
    ),
)
