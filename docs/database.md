# Databases and migrations

This integration is prepared for unreleased Canon 0.13.0.

Install `fastapi-canon[sqlite,migrations]` or
`fastapi-canon[postgres,migrations]`. Add `sqlmodel` when using SQLModel sessions.
The `database` extra installs async SQLAlchemy without choosing a driver. Base
imports and command help do not load SQLAlchemy, Alembic or database drivers.

## Configuration and ownership

Keep environment loading and credentials in application settings:

```python
from pathlib import Path
from fastapi_canon import AlembicMigrations, Database
from app.persistence import metadata
from app.settings import settings

database = Database(
    url=settings.database_url,
    migrations=AlembicMigrations(
        directory=Path(__file__).resolve().parent / "migrations",
        metadata=metadata,
    ),
)
```

`metadata` is SQLAlchemy `MetaData`. Import every application model explicitly
in the module that exports it. With SQLModel, use the application's registered
`SQLModel.metadata`, excluding unrelated test/plugin models when sharing its
global registry. Commands import the database declaration without starting the
FastAPI app; keep that module free of application startup side effects.

Bare `sqlite://` and `postgresql://` URLs select aiosqlite and asyncpg. Canon's
async drivers are `sqlite+aiosqlite` and `postgresql+asyncpg`. SQLAlchemy `URL`
objects are also accepted, including `URL.create` for credentials containing
special characters. Other drivers fail explicitly.

Common synchronous URL aliases (`sqlite+pysqlite`, `postgresql+psycopg`,
`postgresql+psycopg2` and `postgres`) normalize to the selected async driver.
Application settings may keep their environment prefix and secret types; pass
the actual URL value and options such as `echo` at the composition boundary.

```python
from fastapi import FastAPI
from fastapi_canon import Composition

app = Composition(features=[database.feature, orders_feature]).apply(FastAPI())
```

Each application gets its own lazy engine and pool. Composition closes it after
feature lifespans finish, including on startup failure. One declaration can be
reused in independent apps. The first integration supports one database per
container. Models, repositories, SQL and migration history stay application-owned.

`engine_options` passes SQLAlchemy engine options such as `echo`, `pool_pre_ping`,
`pool_size` and `connect_args`; choose options supported by your pool/driver.
SQLite enables foreign keys on every connection and uses explicit BEGIN for
transactional reads, DDL and savepoints. WAL and timeout tuning are explicit
application choices. Do not combine driver-level autocommit or a custom SQLite
isolation mode with this transaction handling.

## Ordinary HTTP routes

```python
from dishka import Provider, Scope, provide
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi_canon import CanonRouter


class OrdersProvider(Provider):
    scope = Scope.REQUEST
    order_repository = provide(SqlOrderRepository, provides=OrderRepository)
    order_service = provide(OrderService)


router = CanonRouter(prefix="/orders", route_class=DishkaRoute)


@router.post("", status_code=201)
async def create_order(
    command: CreateOrderCommand,
    order_service: FromDishka[OrderService],
) -> OrderView:
    return await order_service.create(command)
```

Repositories receive SQLAlchemy `AsyncSession`; services receive repository
interfaces. All dependencies in one request share a session. Repositories may
flush to obtain generated values or surface constraints; the scope owns commit.
For SQLModel, pass `session_class=sqlmodel.ext.asyncio.session.AsyncSession`.
Both SQLModel and SQLAlchemy session annotations then resolve to that same
instance. Sessions use `expire_on_commit=False` and are permanently closed at
scope exit.

`async_sessionmaker[AsyncSession]` is available at APP scope. When configuring a
custom session class, its typed sessionmaker and the SQLAlchemy base annotation
resolve to the same factory. Callers that deliberately use this lower-level
factory own their sessions and transaction boundaries; ordinary services use the
scoped session or a `ServiceScope` instead.

Composition installs one function-scoped HTTP dependency around existing,
contributed and subsequently declared FastAPI routes. Successful handler and
response validation commit before sending the response. Escaping exceptions,
including those rendered as Canon problems, roll back. Returning an explicit
error `Response` without raising is still a successful scope exit; raise an
application exception when the transaction must roll back. An explicit earlier
commit creates an intentional separate transaction boundary.

Request resources close before streaming or background work begins. Materialize
response data before returning; do not retain request-owned sessions/repositories
inside generators or tasks. A DB transaction does not make external HTTP calls,
files or emitted events atomic. Concurrent tasks need separate sessions/scopes.
Remove existing application-level HTTP scope wrappers when adopting this version.
FastAPI yield dependencies that need scoped resources during cleanup should
also use `scope="function"` so their cleanup precedes the shared scope's exit.
WebSocket session/request lifetimes remain managed by Dishka's socket middleware.

## Short scopes for workers and streams

Register a factory at APP scope when a long-lived operation needs fresh,
short-lived dependencies:

```python
from dishka import AsyncContainer, Provider, Scope, provide
from fastapi_canon import ServiceScope


class WorkerProvider(Provider):
    @provide(scope=Scope.APP)
    def prepare_turn_scope(
        self,
        container: AsyncContainer,
    ) -> ServiceScope[PrepareTurn]:
        return ServiceScope(container, PrepareTurn)
```

```python
async with self._prepare_turn_scope() as prepare_turn:
    turn = await prepare_turn(conversation_id)

await self._runtime.process(turn)
```

Each call resolves a fresh service/repository/session graph, commits on success
and rolls back on exceptions. It closes the short scope while leaving the parent
container owned by Composition. Independent scopes do not inherit HTTP/socket
context. Named bindings use `ServiceScope(container, Service, component="name")`.
Ordinary HTTP handlers inject their service directly.

An async generator that resolves dependencies on first iteration can still
resolve APP-scoped dependencies and these factories after the HTTP scope exits.
Resolving a REQUEST dependency at that point is rejected; use the factory to
open a fresh scope instead.

## Alembic workflow

Configure the database declaration in `pyproject.toml`:

```toml
[tool.fastapi-canon.database]
target = "app.database:database"
```

```console
uv run fastapi-canon db init
uv run fastapi-canon db revision --autogenerate -m "create orders"
uv run fastapi-canon db upgrade head
uv run fastapi-canon db check
```

`init` creates `env.py`, Alembic's revision template and `versions/`, refusing to
overwrite a nonempty directory. Revisions are ordinary application-owned Alembic
files with `upgrade()` and `downgrade()`. Review generated changes, especially
renames, data transformations and database-specific types or constraints.

`upgrade` applies the requested revision, defaulting to `head`. `check` requires
current revision heads and fails when Alembic detects unrecorded model/schema
changes. Success exits 0; configuration, dependency and migration failures exit 2.
`--config path/to/pyproject.toml` and `--database module:attribute` go before the
operation. Application imports and relative paths resolve from the configuration
directory, even when invoking the command elsewhere.

Generated environments use the runner-owned connection. Existing Alembic
environments can consume `config.attributes["connection"]` and
`config.attributes["target_metadata"]` using Alembic's connection-sharing recipe.
The runner writes no database URL into revision/INI files and disposes its engine
after each command.

When migrations are configured, startup requires DB revisions to match the
application's heads and checks that owned tables and columns exist. Upgrading to
head repeats this check even when the revision was already current. Startup
never applies revisions or calls `create_all`.
Run upgrades once in a deployment migration job before starting workers.
Table/column presence is not complete schema validation: also run `db check` and
migration tests from an empty DB and from the previous revision with existing data.

Generated table changes use Alembic batch directives for SQLite rebuilds;
PostgreSQL normally emits ALTER statements. Types, constraints, SQL features and
locking still differ between databases. PostgreSQL-specific queries and locks
need real PostgreSQL tests and are not silently emulated on SQLite.

For SQLite batch rebuilds, the runner temporarily disables foreign-key enforcement
outside the migration transaction. It checks all references before committing
and restores enforcement before releasing the connection. Runtime connections
continue to enforce foreign keys normally.

The integration suite runs transaction/migration contracts against both backends.
Set `CANON_TEST_POSTGRES_URL` to an isolated PostgreSQL test database to include
those cases locally; CI also runs them. See the
[runnable database example](../examples/database/README.md).

## Transaction locks

Repositories can inject `TransactionLocks` alongside their session:

```python
from fastapi_canon import TransactionLocks


async def reserve(self, allocation_key: int) -> None:
    await self._transaction_locks.acquire(allocation_key)
    # Read and update the protected records using the injected session.
```

PostgreSQL uses transaction advisory locks on the session's current connection.
They coordinate separate processes and are released by commit or rollback,
including scope cancellation. Applications choose signed 64-bit keys and must
preserve their meaning across deployments. Native `FOR UPDATE`, `SKIP LOCKED`,
savepoints, dialect-specific upserts and `RETURNING` remain available on the
unwrapped SQLAlchemy/SQLModel session.

SQLite cannot provide equivalent advisory locks. `acquire` raises by default.
An application whose isolated SQLite execution deliberately omits these
guarantees can explicitly set `Database(sqlite_advisory_locks="ignore", ...)`.
This is no concurrency substitute. Keep real PostgreSQL locking tests; retain
any application-local mutex until the transaction finishes when its workflow
requires one. Inject the configured lock dependency rather than constructing it
inside repositories so the explicit backend policy is applied consistently.

## Adopting existing persistence infrastructure

| Existing responsibility | Canonical integration |
| --- | --- |
| Engine/pool provider and disposal | `Database.feature` |
| Session provider and commit/rollback | Shared request-scoped `AsyncSession` |
| HTTP dependency-scope wrapper | Composition's central function scope |
| Short worker/socket operation | APP-scoped `ServiceScope[Service]` factory |
| Typed factory for deliberate standalone work | `async_sessionmaker[SessionClass]` |
| PostgreSQL advisory transaction locks | Injected `TransactionLocks` |
| Alembic directory and existing revision graph | `AlembicMigrations`, preserving application revisions |
| Missing schema behind a current revision | Presence checks at startup and upgrade to head; add a forward repair revision |
| Readiness | Inject the owned `AsyncEngine` into the application's check |
| Settings, model base classes and domain/repository mapping | Remain application-owned and consume these standard resources |

Existing repositories keep `get`, `merge`, `flush`, `exec`/`execute`, eager-load
options and native SQL. Existing migration environments keep the borrowed-
connection protocol. No Canon entity base class, generic repository, lock-key
convention, settings prefix or deployment platform is imposed.
