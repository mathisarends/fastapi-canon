# Database example

From the repository root, install the locked development dependencies, apply the
checked-in Alembic revision, then start FastAPI:

```console
uv sync --frozen --all-groups
uv run fastapi-canon db upgrade head
uv run uvicorn examples.database.app:app
```

POST `{"name":"first"}` to `/items`. The handler only calls its injected
service; the request scope commits before the 201 response is sent. Restarting
the app preserves the row in `example.sqlite3`.

Set `CANON_EXAMPLE_DATABASE_URL` to `postgresql+asyncpg://...` to use PostgreSQL
with the same providers and migration. `uv run fastapi-canon db check` checks
revision heads and model/schema drift. To change the model, review the revision
from `uv run fastapi-canon db revision --autogenerate -m "describe change"`,
then apply it with `db upgrade head`.

The [database guide](../../docs/database.md) covers SQLModel sessions,
short-lived worker scopes and deployment ownership.
