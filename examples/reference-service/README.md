# Reference service

A small task API demonstrating the canon's feature slice, repository, mapping, transaction, and dependency-injection patterns.

The example defaults to SQLite for convenient local use. Persistent schemas should use Alembic; `create_schema.py` exists only to bootstrap this disposable example. See [Database migrations](../../guides/migrations.md) for the preferred layout.

```console
uv sync --all-groups
uv run python create_schema.py
uv run uvicorn app.main:app --reload
```

The task API is available under `/api/v1/tasks`; interactive documentation is at `/docs`. Task routes require `Authorization: Bearer local-development-token`. This token is a local example default only; deployed services inject real authentication configuration and a production `TokenVerifier` adapter.
