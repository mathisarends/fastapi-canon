# Resources, persistence and scopes

Use this before adding a database, changing transactions or moving work into a
stream, socket or worker. The detailed API authority is [database.md](https://github.com/mathisarends/fastapi_canon/blob/fastapi-canon-v0.13.0/docs/database.md).

## Ownership map

| Lifetime | Owns | Typical consumer |
| --- | --- | --- |
| Application / `Scope.APP` | Engine/pool, pooled HTTP clients, configuration, factories | Composition-owned providers |
| HTTP function / `Scope.REQUEST` | Session/transaction, repositories, request workflow | Directly injected HTTP service |
| Independent short scope | Fresh service/repository/session graph for one operation | Stream iteration, worker or long-lived runtime |

In Canon 0.13, HTTP resources close after handler and response validation, before
the response is sent. The database integration commits a successful scope and
rolls back an escaping exception, including an exception rendered as a Canon
problem. Returning an error `Response` is still a successful exit; raise an
application exception when rollback is required.

## Database setup

Choose `fastapi-canon[sqlite,migrations]` or
`fastapi-canon[postgres,migrations]`; add `sqlmodel` only when using SQLModel.
Keep environment loading and defaults in Pydantic settings. Providers assemble
those settings without overriding them with hidden literal defaults.

Declare `Database(url=settings.database_url, migrations=AlembicMigrations(...))`
in an application-owned module. Export its registered SQLAlchemy metadata and
explicitly import the ORM models that contribute tables. Compose
`database.feature` before features that need sessions, using
`Composition(features=(database.feature, projects_feature))`.

- Inject `sqlalchemy.ext.asyncio.AsyncSession` into SQL repositories and expose
  repository interfaces to services. With SQLModel, configure
  `Database(..., session_class=sqlmodel.ext.asyncio.session.AsyncSession)`.
- Repositories may `flush()` for IDs and constraints. The ordinary HTTP scope
  owns commit/rollback; do not commit inside each repository method.
- Map ORM state to materialized application data before leaving the scope. Do not
  depend on lazy loading during serialization or share a session across tasks.
- Composition owns engine cleanup, including startup failures. Do not add an
  overlapping manual HTTP request scope wrapper or a second container.
- Database transactions do not make external HTTP, files or emitted events
  atomic. Introduce the required consistency mechanism for the actual workflow.

For persistent schemas use application-owned Alembic history. Configure
`[tool.fastapi-canon.database] target = "backend.platform.database:database"`
with the actual importable declaration, then use `fastapi-canon db init`,
`db revision --autogenerate -m "..."`, `db upgrade head` and `db check`.
Review generated revisions. The reference service's disposable schema bootstrap
is not a deployment migration strategy.

## Long-lived operations

Do not capture an HTTP request's session or repository in an async generator,
background task or WebSocket session. A generator's body runs after HTTP response
start, including code before its first yield.

For a long-lived worker, expose a typed factory at APP scope in the provider
that assembles that worker. `PrepareTurn` below stands for an application-owned
injectable service with a complete registered dependency graph:

```python
from dishka import AsyncContainer, Provider, Scope, provide
from fastapi_canon import ServiceScope


class WorkerProvider(Provider):
    @provide(scope=Scope.APP)
    def prepare_turn_scope(
        self, container: AsyncContainer
    ) -> ServiceScope[PrepareTurn]:
        return ServiceScope(container, PrepareTurn)
```

The worker receives `ServiceScope[PrepareTurn]`, then uses
`async with self._prepare_turn_scope() as prepare_turn:` for each short database
phase. Materialize its result, close the scope, and perform long network/model
work afterwards. Each call gets an independent graph with commit/rollback and
cleanup. It does not inherit HTTP/socket context; pass needed identity explicitly.
Ordinary HTTP handlers inject their service directly.

When adapting an existing application, preserve a correct established unit of
work unless migration is part of the task. Do not nest automatic and manual
transaction owners accidentally. Verify persistence from a fresh scope after the
producing scope closes.

## Other resources

Create pooled HTTP clients once per intended owner through a yielding provider;
close them on that owner's exit. Adapters borrow these clients, set credentials
per request and return typed application data. Avoid one client per API call or
closing a borrowed client. Give concurrent tasks independent SQL scopes, bounded
lifetimes and cancellation cleanup. Preserve disconnect cancellation instead of
converting it into a business failure.
