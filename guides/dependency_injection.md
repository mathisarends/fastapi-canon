# Dependency Injection

Use dependency injection to make object construction, lifetimes, and adapter selection explicit. It is a composition technique, not a service-locator API for business code.

## Constructor injection

Application services declare collaborators in their constructors and depend on inward-facing ABCs:

```python
class TaskService:
    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository
```

Do not construct infrastructure adapters in services, pass a container into them, or resolve dependencies dynamically. Explicit constructors keep use cases framework-independent and reveal dependency growth.

Use the project's [ABC convention](architecture.md#abcs-for-owned-ports) for interfaces it owns. Providers return the ABC while constructing a concrete implementation:

```python
@provide(scope=Scope.REQUEST)
def repository(session: AsyncSession) -> TaskRepository:
    return SqlTaskRepository(session)
```

## Dishka and FastAPI responsibilities

Use Dishka for application dependencies: repositories, services, settings, database resources, and external adapters. Use FastAPI `Depends` for values or behavior derived from the HTTP connection: authentication credentials, headers, cookies, query policy, and request context.

```python
AuthenticatedUserId = Annotated[UUID, Depends(authenticated_user_id)]

async def endpoint(
    user_id: AuthenticatedUserId,
    service: FromDishka[TaskService],
) -> TaskResponse: ...
```

FastAPI and Dishka types stop at the presentation layer. Pass plain values and application types to services.

## Scopes

Choose the narrowest lifetime that matches the dependency:

| Scope | Typical dependencies |
| --- | --- |
| application | settings, async engine, connection pools, stateless API clients |
| request | database session, repositories, application services, request caches |
| explicit task/session | long-running jobs, streaming or WebSocket resources |

Do not make mutable request state application-scoped. Do not retain request-scoped objects in background tasks or WebSockets. Long-lived work receives a factory or opens a dedicated DI scope and transaction.

Resource providers use `yield` and own cleanup. The FastAPI lifespan closes the application container so application-scoped engines and clients are disposed.

## Composition root

Create the container and FastAPI application in one root module. That module is allowed to know concrete implementations; domain and application modules are not.

Represent each feature's integration surface as data:

```python
@dataclass(frozen=True, slots=True)
class Feature:
    routers: Sequence[APIRouter] = ()
    providers: Sequence[type[Provider]] = ()
    register_exception_handlers: Callable[[FastAPI], None] | None = None
```

The root iterates an explicit feature list to assemble providers, routers, and exception handlers. This prevents `main.py` from importing every feature internal and keeps registration uniform. It is not a runtime plugin-discovery system; ordering remains visible and deterministic.

Avoid import-time network connections and database work. Container construction may describe resources, but providers acquire them within managed scopes.
