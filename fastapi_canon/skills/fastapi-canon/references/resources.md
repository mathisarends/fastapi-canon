# Resources and scopes

| Scope | Owns |
| --- | --- |
| `Scope.APP` | Engine/pool, pooled HTTP clients, settings, scope factories |
| `Scope.REQUEST` | Session/transaction, repositories, the request's services |
| `Scoped` / `ServiceScope` | A fresh REQUEST graph for one worker or stream step |

A request commits on success and rolls back when an exception escapes,
including declared errors. Returning an error `Response` counts as success.

## Database

Install `fastapi-canon[sqlite,migrations]` or `[postgres,migrations]`.

```python
from fastapi_canon.integrations.database import AlembicMigrations, Database, Tables

database = Database(url=settings.database_url, migrations=AlembicMigrations(...))

Feature(name="projects", ..., integrations=[Tables(ProjectRow)])
Composition(features=FEATURES, integrations=[database])
```

- Repositories receive `AsyncSession`, may `flush()`, and never commit.
- Map rows to plain data before the scope closes; no lazy loading afterwards.
- A transaction does not make external HTTP, files or events atomic.
- SQLModel: `Database(..., session_class=sqlmodel.ext.asyncio.session.AsyncSession)`.

Migrations use application-owned Alembic history:

```toml
[tool.fastapi-canon.database]
target = "backend.app:create_app"
```

```console
uv run fastapi-canon db revision --autogenerate -m "create projects"
uv run fastapi-canon db upgrade head
uv run fastapi-canon db check
```

Review generated revisions. Run `db init` once for a new project.

## Background work and streams

Stream generators run after the response starts; tasks and sockets outlive the
request. Never capture a request session there. Inject a `Scoped` use case
instead — each call gets its own session and transaction:

```python
from dishka import AsyncContainer, Provider, Scope, provide
from fastapi_canon import Scoped


class TurnProvider(Provider):
    prepare_turn = provide(PrepareTurn, scope=Scope.REQUEST)

    @provide(scope=Scope.APP)
    def prepare(self, container: AsyncContainer) -> Scoped[PrepareTurn]:
        return Scoped(container, PrepareTurn)
```

`plan = await prepare(turn_id)` commits and closes before slow work starts. Use
`ServiceScope` (`async with scope() as service:`) when several calls share one
transaction. Pass identity explicitly; scopes do not inherit request context.

Create pooled HTTP clients once in a yielding APP provider; adapters borrow
them. Preserve cancellation on disconnect instead of turning it into a failure.

Details: [database guide](https://github.com/mathisarends/fastapi-canon/blob/main/docs/09-database.md).
