# Reference service

A small task API demonstrating the canon's feature slice, repository, mapping, transaction, and dependency-injection patterns.

The example defaults to SQLite for convenient local use. Production services should use Alembic migrations and their production database driver; `create_schema.py` exists only to bootstrap this example.

```console
uv sync --all-groups
uv run python create_schema.py
uv run uvicorn app.main:app --reload
```

The API is available under `/api/v1/tasks`; interactive documentation is at `/docs`.
