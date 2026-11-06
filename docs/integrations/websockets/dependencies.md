# Dependency injection

Application objects do not belong in the public request schema. Mark them with
`Inject[T]`; fastapi_canon.integrations.websockets resolves them by their concrete type and passes them to
the handler.

```python
from fastapi_canon.integrations.websockets import Inject, RpcChannel, RpcModel


class CreateTask(RpcModel):
    title: str


class TaskStore:
    async def create(self, title: str) -> int:
        return 1


tasks = RpcChannel("tasks")


@tasks.method()
async def create(params: CreateTask, store: Inject[TaskStore]) -> int:
    return await store.create(params.title)
```

`store` is invisible to callers and to OpenRPC. Only `CreateTask` is serialized
on the wire.

## Context terms

Canon uses "context" for two different mechanisms. Both are named after what they
supply to handlers, not after when they run:

| Term | Declared with | When it runs | Supplies |
| --- | --- | --- | --- |
| Connection context | `include_channel(..., context=async_function)` or `CanonWebSocketRouter` `context=` | Once per WebSocket, before acceptance | One typed value from the function's return annotation, available as `Inject[T]` |
| Hook | `hooks=(function, ...)` next to a connection context | During that preparation, only when reachable | Reusable typed values, looked up by return type; requires `context=` |
| Injected service | Dishka provider or a `resolver=` | On demand when a handler asks for `Inject[T]` | Application services such as repositories |
| Server values | `channel.create_server(context=object_or_mapping)` | Fixed when the server is built | Known objects, by concrete type or by an explicit type mapping; for small applications and tests |

A connection context is a function that prepares a value per connection. Server
values are plain objects supplied up front, and they take precedence over a
resolver. The keyword is the same only because each supplies "context" values to
handlers; the parameters accept different shapes and are not interchangeable.
If you need per-connection admission, authentication or resource preparation, use
`include_channel(..., context=function)`. If you only have a few known objects in
a test or script, pass them to `create_server(context=...)`. Do not pass a
connection-context function there: `create_server` registers it as a plain object
rather than calling it.

## Pass known values as context

For small applications and tests, pass an object or a type-to-value mapping to
the channel or endpoint server:

```python
store = TaskStore()
server = tasks.create_server(context={TaskStore: store})
response = await server.handle(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tasks.create",
        "params": {"title": "Write docs"},
    }
)
```

A single object is registered under its concrete type. A mapping is useful
when several dependencies are needed or a value is registered against a base
class or protocol.

## Provide a resolver

A resolver implements one async method:

```python
class Resolver:
    async def resolve[T](self, dependency: type[T]) -> T: ...
```

Pass it as `resolver=` to `include_channel`, a protocol-level endpoint, or
`create_server()`. A synchronous or asynchronous callable taking the
requested type is accepted as a lightweight alternative.

Context values take precedence over the resolver. `RpcConnection` is also made
available by type for the lifetime of that connection.

## Resource scopes

Every method enters its channel's resolver scope. The default `call_scope`
looks for an optional `enter_scope()` async context manager on the resolver,
which makes request-scoped cleanup possible:

```python
channel = RpcChannel("tasks", resolver_scope=call_scope)
```

After acceptance, fastapi_canon.integrations.websockets optionally enters the resolver's
`enter_connection()` context for the socket lifetime. Event sources live in
that connection scope; binary streams additionally enter their channel's
resolver scope.

## Dishka

Dishka ships as a Canon base dependency. Select the WebSocket requirements and
wrap an async Dishka container:

```bash
uv add "fastapi-canon[websockets]"
```

```python
from fastapi_canon.integrations.websockets.adapters.dishka import DishkaResolver

resolver = DishkaResolver(container)
```

The adapter maps a connection to Dishka's `SESSION` scope and each RPC call to
a child scope. Endpoints without an explicit resolver read the app's root
container at connection time when one is installed, as `Composition` does.

```python
from dishka.integrations.fastapi import FromDishka
from fastapi_canon import APIRouter


async def open_session(
    session_id: UUID,
    actor: AuthenticatedActor,
    sessions: FromDishka[SessionRepository],
) -> Session:
    return await sessions.get_authorized(session_id, actor)


router = APIRouter()
router.include_channel(
    session_events,
    path="/sessions/{session_id}/events",
    context=open_session,
    rejects=[session_not_found],
)
```

The context function can declare `FromDishka[T]` parameters without `@inject`.
`Composition` installs Dishka from feature providers. A manually constructed
`DishkaResolver` needs the APP container.

Handlers receive the session as `Inject[Session]` and everything else from
Dishka. The context also joins Dishka's SESSION context, so providers can
receive it through `from_context`.

Dishka's FastAPI middleware from `setup_dishka()` opens its own SESSION container
for every WebSocket. Context preparation borrows it to open a short child
REQUEST scope. Accepted RPC handlers resolve from the separate SESSION scope
opened by the RPC runtime, so SESSION-scoped provider instances are not shared
between admission and serving. Pass prepared data through the typed context and
use `Inject[T]` in RPC handlers instead of accessing
`websocket.state.dishka_container` directly.

## Reusable admission hooks

Use the root `Inject[T]` annotation in context functions as well as handlers.
Register reusable context hooks once by function; their concrete return
annotations supply the lookup keys:

```python
from fastapi_canon import APIRouter, Inject


async def authorized_project(
    project_id: UUID,
    user: Inject[CurrentUser],
    resolver: Inject[ProjectResolver],
) -> AuthorizedProject:
    project = await resolver.resolve(
        project_id=project_id, user_id=user.id, user_role=user.role
    )
    return AuthorizedProject(project_id=project.id)


async def screencast_connection(
    session_id: UUID,
    project: Inject[AuthorizedProject],
    screencast: Inject[ProjectBrowserScreencast],
) -> ScreencastConnection:
    return ScreencastConnection(
        await screencast.open_required(project.project_id, session_id)
    )


router = APIRouter(prefix="/projects")
router.include_channel(
    session_events,
    path="/{project_id}/sessions/{session_id}",
    context=screencast_connection,
    hooks=(authorized_project,),
)
```

The application supplies the domain types and service methods in this example.
Binary streams and raw sockets retain their specialized WebSocket endpoints.
Hooks can use normal FastAPI dependencies and path/header/query annotations.
Their injected dependencies form a graph, independent of tuple order. Each
reachable hook runs once per context preparation; shared dependencies reuse its
result. Duplicate hook result types, dependency cycles and generator hooks fail
at registration. A registered hook takes precedence over DI for its result type;
its errors propagate without a fallback. Unused hooks are not executed.

Dishka context preparation enters a REQUEST scope for the entire preparation and closes
it before acceptance, including on failure or cancellation. An existing
middleware SESSION is borrowed; its container is restored after preparation.
Application generator providers acquire and clean up resources in that REQUEST
scope. There is no additional hook scope setting, UnitOfWork abstraction or
resource decorator. A resource required until disconnect belongs in SESSION.

Context and hook results must remain usable after preparation ends. Handlers
can inject them by their declared types, including using them as SESSION
`from_context` inputs. REQUEST-owned objects themselves must not escape through
these results. The same preparation applies to binary stream endpoints and
custom handlers declared with the same `context=` and `hooks=`.

## Authenticated connections

Use `router.include_channel(channel, path=..., context=async_dependency, rejects=[...])`
when authentication needs FastAPI dependencies, cookies, origins or path values.
The context return annotation supplies its injection type.

The executable [authenticated example](../../../examples/websockets/authenticated.py) checks an
Origin allowlist and a session cookie before accepting. Typed rejection bindings
produce HTTP 401/403. A custom Dishka resolver arms `RpcConnection.expire_at()`
from the authenticated login, closing an expired connection with
`1008 / credentials_expired`. The APP session store is shared; the login belongs
to the RPC SESSION and request identities belong to each call's REQUEST scope.
The example owns and closes its APP container in the FastAPI lifespan.

Run from the repository root with
`uv run --frozen uvicorn examples.websockets.authenticated:app`.
The in-memory demo session is for illustrating integration; use the application's
session service and explicit production Origin allowlist. The tests cover denied
handshakes, an authenticated call, and expiry of an established connection.

[Back to documentation](index.md)
