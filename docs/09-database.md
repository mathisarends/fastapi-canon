# 9. Database

Let Canon own the engine, per-request transactions and Alembic migrations.
Install `fastapi-canon[postgres,migrations]` or `fastapi-canon[sqlite,migrations]`;
add `sqlmodel` for SQLModel sessions.

## Configuration and ownership

```python
from fastapi_canon.integrations.database import AlembicMigrations, Database, Tables

database = Database(
    url=settings.database_url,
    migrations=AlembicMigrations(directory=Path(__file__).parent / "migrations"),
)

orders = Feature(
    name="orders",
    routers=[router],
    providers=[OrderProvider],
    integrations=[Tables(OrderRow, OrderLineRow)],
)
app = Composition(features=[orders], integrations=[database]).build(FastAPI())
```

Each feature contributes its tables. `postgresql://` and `sqlite://` URLs select
asyncpg and aiosqlite. The engine is created lazily and disposed on shutdown;
models, SQL and migration history stay yours. Startup checks that the database
is migrated but never migrates it.

| Option | Use |
| --- | --- |
| `engine_options={...}` | SQLAlchemy engine options such as `pool_size` |
| `session_class=` | `sqlmodel.ext.asyncio.session.AsyncSession` for SQLModel |
| `check_migrations_on_startup=False` | when deployments check migrations separately |
| `engine_context=` | attach engine listeners for the engine's lifetime |

## One transaction per request

```python
class OrderProvider(Provider):
    scope = Scope.REQUEST
    repository = provide(SqlOrderRepository, provides=OrderRepository)
    service = provide(OrderService)


@router.post("/orders", status_code=201)
async def create_order(
    command: CreateOrder, service: Inject[OrderService]
) -> OrderView:
    return await service.create(command)
```

Repositories take an `AsyncSession`. Every dependency in a request shares it.
The request commits on success and rolls back when an exception escapes,
including declared errors.

## Short scopes for workers and streams

Long-running work must not hold a request session. Inject a `Scoped` use case,
and each call gets its own session and transaction:

```python
class TurnProvider(Provider):
    prepare_turn = provide(PrepareTurn, scope=Scope.REQUEST)

    @provide(scope=Scope.APP)
    def prepare(self, container: AsyncContainer) -> Scoped[PrepareTurn]:
        return Scoped(container, PrepareTurn)


async def process_turn(
    turn_id: str, prepare: Scoped[PrepareTurn], engine: TurnEngine
) -> None:
    plan = await prepare(turn_id)  # commits and closes its session
    await engine.run(plan)  # no database connection held here
```

Use `ServiceScope` when several calls must share one transaction:
`async with service_scope() as service: ...`.

## Migrations

```toml
[tool.fastapi-canon.database]
target = "app.main:create_app"
```

```console
uv run fastapi-canon db init
uv run fastapi-canon db revision --autogenerate -m "create orders"
uv run fastapi-canon db upgrade head
uv run fastapi-canon db check
```

Revisions are ordinary Alembic files. Run `upgrade` once per deployment, before
the workers start.

## Locks

```python
await acquire_lock(allocation_key)  # an injected AcquireTransactionLock
```

On PostgreSQL this is a transaction advisory lock, released on commit or
rollback.

Runnable: [`examples/database`](../examples/database/README.md).

---

Previous: [8. File transfers](08-file-transfers.md) · [Learning path](README.md) · Next: [10. Observability](10-observability.md)
