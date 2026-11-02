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

Pass it as `resolver=` to `RpcRouter`, `RpcRouter.serve()`, an endpoint,
or `create_server()`. A synchronous or asynchronous callable taking the
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
a child scope. Pass `resolver=Dishka()` to the router to read the app's root
container at connection time.

```python
from dishka.integrations.fastapi import FromDishka
from fastapi_canon.integrations.websockets import RpcRouter
from fastapi_canon.integrations.websockets.adapters.dishka import Dishka


async def open_session(
    session_id: UUID,
    actor: AuthenticatedActor,
    sessions: FromDishka[SessionRepository],
) -> Session:
    return await sessions.get_authorized(session_id, actor)


rpc = RpcRouter(
    context=open_session,
    resolver=Dishka(),
    rejects=[session_not_found],
)
rpc.stream("/sessions/{session_id}/screencast", frames)
rpc.socket("/sessions/{session_id}/events", channels=[session_events])
web.include_router(rpc)
```

The context function can declare `FromDishka[T]` parameters without `@inject`.
Install Dishka through `Composition` and feature providers, or configure
`setup_dishka()` and the container lifespan explicitly. A manually constructed
`DishkaResolver` needs the APP container.

Handlers receive the session as `Inject[Session]` and everything else from
Dishka. The context also joins Dishka's SESSION context, so providers can
receive it through `from_context`.

Dishka's FastAPI middleware from `setup_dishka()` still opens its own SESSION
container for every WebSocket, including RPC sockets. fastapi_canon.integrations.websockets does not use
that container: RPC handlers resolve from the SESSION scope opened per
connection above, so session-scoped values are never shared between the two.
Keep RPC dependencies in the fastapi_canon.integrations.websockets scope and do not rely on
`websocket.state.dishka_container` in RPC code.

## Authenticated connections

Use `RpcRouter(context=async_dependency, resolver=Dishka(), rejects=[...])`
when authentication needs FastAPI dependencies, cookies, origins or path values.
The context return annotation supplies its injection type.

The executable [authenticated example](../../../examples/websockets/authenticated.py) checks an
Origin allowlist and a session cookie before accepting. Typed rejection bindings
produce HTTP 401/403. A custom Dishka resolver arms `RpcConnection.expire_at()`
from the authenticated login, closing an expired connection with
`1008 / credentials_expired`. The APP session store is shared; the login belongs
to the RPC SESSION and request identities belong to each call's REQUEST scope.
The example owns and closes its APP container in the FastAPI lifespan.

Run from this package directory with `uv run uvicorn examples.websockets.authenticated:app`.
The in-memory demo session is for illustrating integration; use the application's
session service and explicit production Origin allowlist. The tests cover denied
handshakes, an authenticated call, and expiry of an established connection.

[Back to documentation](index.md)
