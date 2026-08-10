# Database

## Session ownership

The request scope owns the transaction. A repository participates in it but never finalizes it:

```python
@provide(scope=Scope.REQUEST)
async def session(factory: async_sessionmaker[AsyncSession]):
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

This permits several repositories to join one atomic use case. Call `flush()` inside a repository when an operation needs generated identifiers or constraint evaluation before returning.

For work whose lifetime is longer than a request, inject a session factory and create an explicit short transaction. Never retain a request session in background tasks, streams, or WebSocket state.

## Repository contracts

Put the contract in the domain and describe intent:

```python
class TaskRepository(ABC):
    @abstractmethod
    async def get_by_id(self, *, task_id: UUID) -> Task | None: ...

    @abstractmethod
    async def save(self, *, task: Task) -> Task: ...
```

Avoid exposing `filter_by(**kwargs)`, SQL expressions, sessions, or ORM models through the domain contract. Add a method when it represents a use-case-relevant operation.

## Generic SQL mechanics

A generic infrastructure base can safely centralize identical mechanics such as lookup by primary key, merge, delete, and existence checks. Bind both ORM and domain types, and require explicit mapping:

```python
class SqlRepository[ModelT: DatabaseModel, DomainT](ABC):
    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None: ...

    @abstractmethod
    def to_domain(self, model: ModelT) -> DomainT: ...

    @abstractmethod
    def to_model(self, entity: DomainT) -> ModelT: ...
```

Feature repositories inherit the base and the domain contract. Specialized operations stay in the feature repository. Do not grow the base into a universal query language.

Import and inherit the feature's repository ABC in every SQL implementation. Do not rely on accidental structural compatibility: the explicit base class documents the adapter's role and lets static analysis catch missing abstract operations.

## SQLModel boundaries

Use `SQLModel` only for table mapping. Do not use a `table=True` model as a request schema or domain entity. Database nullability, generated defaults, relationship loading, and API optionality are separate concerns.

Prefer database-generated timestamps and identifiers where the database must be authoritative. Refresh after flush when returning those values. Use timezone-aware timestamp columns.

Load relationships deliberately. Repository methods must return fully usable domain values and must not rely on lazy loading after the session closes.

## Concurrency

Use database constraints as the final integrity boundary. For read-modify-write behavior, select with `FOR UPDATE` or use an atomic statement as appropriate. Handle expected uniqueness races explicitly, usually with a savepoint, without rolling back unrelated work in the outer transaction.

## Migrations

- Import all table metadata in Alembic's environment before autogeneration.
- Review generated migrations; do not treat autogeneration as schema design.
- Give constraints and indexes stable names.
- Keep migrations forward-compatible with the deployed application sequence.
