# Services and channels

JSON-RPC methods accept parameters by name as a JSON object. Positional
parameter arrays are not supported.

A channel owns related operations and their wire namespace. The feature's
`APIRouter` mounts channels on concrete socket paths with `include_channel`.
Keeping those concepts separate lets one application expose several
independently addressable APIs while still producing one contract.

## Define a method

Methods are async free functions. Their public input is either absent or one
Pydantic model, and their return annotation describes the result.

```python
from fastapi import FastAPI

from fastapi_canon import APIRouter, Composition, Feature, export_contracts
from fastapi_canon.integrations.websockets import RpcChannel
from fastapi_canon.integrations.websockets import RpcModel


class CreateTask(RpcModel):
    title: str


class Task(RpcModel):
    id: int
    title: str


tasks = RpcChannel("tasks")


@tasks.method()
async def create(params: CreateTask) -> Task:
    return Task(id=1, title=params.title)


@tasks.method()
async def health() -> None:
    return None


router = APIRouter()
router.include_channel(tasks, path="/rpc")
feature = Feature(name="tasks", routers=[router])
app = Composition(features=(feature,)).build(FastAPI(title="Tasks"))
contract = export_contracts(
    app, kinds=("openrpc",), base_url="wss://api.example.com"
).openrpc
```

The methods are exposed as `tasks.create` and `tasks.health`. Use an explicit
decorator name when the Python and wire names should differ:

```python
@tasks.method("list", summary="List the current tasks.")
async def list_tasks() -> list[Task]:
    return []
```

If `summary` is omitted, the first line of the function docstring becomes the
contract summary. Handlers may add any number of injected parameters after the
optional params model; see [Dependency injection](dependencies.md).

## Namespaces

By default, the channel name is also its namespace. Set a different namespace,
or use an empty one for root-level method names:

```python
admin = RpcChannel("admin", namespace="internal.admin")
system = RpcChannel("system", namespace="")
```

Channels, operations, and namespaces must remain unambiguous across a service.
fastapi_canon.integrations.websockets rejects duplicate names and cases where an operation is also the
prefix of another operation when the protocol is materialized.

## Mount endpoints

Mount a channel with `router.include_channel(channel, path=...)` on the
feature's `APIRouter`. Each call mounts a snapshot of the channel as one JSON-RPC
WebSocket endpoint, so declare operations before mounting. The router contributes
the channel's error registries to `Feature`, and `export_contracts(app)` exports
the same problem types served at runtime.

For a per-connection FastAPI dependency, pass its async function as `context=`.
Its concrete return annotation supplies the type available to handlers through
`Inject[T]`. Under `Composition`, `Inject[T]` also resolves application services
from the shared Dishka container.

To serve several channels on one socket, include them in a root channel and
mount that:

```python
project = RpcChannel("project", namespace="")
project.include_channel(tasks, namespace="tasks")
project.include_channel(admin, namespace="internal.admin")

router.include_channel(
    project,
    path="/projects/{project_id}/rpc",
    name="project-rpc",
    subprotocol="rpc.v2",
    summary="Project control API.",
    version=2,
)
```

Path variables are available from `RpcConnection.path_params`. Endpoint names
identify servers in OpenRPC and generated clients; when omitted, the name is
derived from the full path, such as `rpc_ws` for `/ws`.

Use child channels for nested wire namespaces. Mount only the root; its children
inherit declared errors and the resolver scope:

```python
voice = RpcChannel("voice", raises=(ResourceNotFoundError,))
turn = voice.child("turn")


@turn.method()
async def start() -> None: ...  # voice.turn.start


router.include_channel(voice, path="/rpc")
```

A single nested operation does not need a child channel. Names passed to the
decorators are relative to the channel namespace and may contain dots; each
segment is validated:

```python
@voice.method("settings.update")
async def update_settings() -> None: ...  # voice.settings.update
```

Use a child channel when several operations share a namespace or need their
own `raises=` or `resolver_scope=`; local bindings override inherited bindings
for the same domain exception. A dotted name uses its channel's
configuration.

For a standalone dotted namespace, the channel name can default to it:
`RpcChannel(namespace="voice.turn")`.

`include_channel` mounts a snapshot. Later registrations on the source channel
do not change an endpoint that is already mounted.

## Protocol version

`include_channel(..., version=...)` takes a positive integer. The version is emitted as
`<version>.0.0` in OpenRPC, so changing a wire contract can be reflected in the
service definition and generated artifacts together.

Serving options configure the mounted connection:

```python
router.include_channel(
    tasks,
    path="/rpc",
    error_mapper=map_error,
    limits=RpcLimits(max_concurrency=16),
)
```

Malformed JSON, including input nested beyond the JSON decoder's recursion
limit, produces `parse_error` (`-32700`) and leaves an RPC connection usable.
The same handling applies to `RpcServer.handle_json()` for custom transports.
Recursive parameter models continue to report Pydantic validation failures as
`invalid_params`; arbitrary nested values accepted by `Any` are not rejected
solely for their depth.

Pending-limit rejections log at most one warning per connection. On connection
cleanup, a debug message reports their total. This remains bounded with debug logging
enabled; request middleware still receives each rejection.

## Request deadlines

Set `RpcLimits(request_timeout=30.0)` on a service, endpoint, or standalone
server to bound each admitted request. The default is `None`, preserving existing
long-running methods until the application chooses a budget. Values are finite,
positive seconds.

```python
@tasks.method(timeout=5.0)
async def quick_lookup() -> str:
    return "ready"


@tasks.method(timeout=None)
async def long_running() -> None:
    await finish_long_running_work()


router.include_channel(tasks, path="/rpc", limits=RpcLimits(request_timeout=30.0))
```

An omitted method override inherits the serving limit; explicit `None` disables
it. Socket deadlines start at per-item admission using the event loop's monotonic
clock. Scheduling, middleware entry and exit, semaphore wait, dependency
resolution, and method execution consume that budget. `RpcServer.handle()` and
`handle_json()` use the same rules, starting at dispatch. The timer can interrupt
middleware initialization and teardown; ordinary response queueing and transport
sending use `send_timeout`.

Expiry cancels the invocation, releases its concurrency and pending slots after
cleanup, and answers calls with `request_timeout` (`-32000`). Notifications have
no response. `RpcRequestTimeoutError` is a built-in server error, so it survives
`strict_errors=True` without a `raises=` declaration. Middleware scopes
receive that error distinctly from an internal failure. Cancellation is cooperative:
handlers and dependency scopes must propagate `asyncio.CancelledError`; synchronous
CPU work cannot be preempted, and cleanup can extend elapsed time beyond the budget.

Message streams apply the serving deadline and semaphore to the open or
cancel handshake, including queueing its acknowledgement. The stream
iterator and its dependency scope have their own lifetime and continue after a
successful handshake. End a stream with cancel, not request cancellation.

See [Request cancellation](protocol.md#request-cancellation) for the wire control
used to stop a queued or executing call. Standalone `handle()` transports can
cancel their own invocation task, but must implement their own request registry
to interpret that notification.

## Pending work per connection

`max_pending_requests` counts admitted request and notification items, including
each batch member, while they wait for concurrency capacity or execute. Admission
reserves slots synchronously in wire order before scheduling work. A batch can be
partially admitted: excess calls return `pending_limit` in the batch response;
excess notifications are dropped without a response. Invalid items retain
`invalid_request`, and empty or oversized batches remain invalid requests.

Reservations are released when dispatch finishes or is cancelled, with fallback
cleanup for tasks cancelled before they start. Executing items count toward both
pending capacity and `max_concurrency`. Response collectors are separately bounded
by `max_pending_requests + max_queue_size`, and outgoing frames use the bounded
queue and send timeout. Responses to server-to-client calls bypass admission,
including responses received inside batches. The reader checks this control path
before reserving work; `$/cancelRequest` uses the same location and does not need
a handler slot. Invalid requests, parse failures, and admission rejections run
in bounded response collectors so a blocked outgoing queue cannot block the
reader. Once collector capacity is exhausted, additional ordinary messages close
the connection with `POLICY_VIOLATION`; cancellation controls still bypass that
check.

The default pending limit remains 1024. This fixes the former batch multiplier,
so workloads depending on that accidental extra capacity may now receive
`pending_limit` earlier. Configure a larger explicit limit only after estimating
the associated memory and handler costs.

## Capacity across connections

`RpcLimits` is per connection. Share one application-owned middleware instance
within a worker to bound connection admission and concurrent request execution.
The connection scope owns permits through cleanup; `before_accept` checks its
decision and rejects excess handshakes. A handshake hook alone cannot release a
connection permit on disconnect. Middleware scopes are observational and contain
ordinary hook errors, so do not raise the rejection inside the middleware itself.

```python
import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi_canon.integrations.websockets import (
    RpcConnectionScope,
    RpcHandshake,
    RpcMiddleware,
    RpcReject,
    RpcRejection,
    RpcRequestScope,
)


class SharedCapacity(RpcMiddleware):
    def __init__(self, *, connections: int, requests: int) -> None:
        if connections < 1 or requests < 1:
            raise ValueError("Capacity must be positive")
        self.active_connections = 0
        self.active_requests = 0
        self._connection_limit = connections
        self._requests = asyncio.Semaphore(requests)
        self._admitted = ContextVar("connection_admitted", default=False)

    @asynccontextmanager
    async def connection(self, scope: RpcConnectionScope) -> AsyncGenerator[None, None]:
        admitted = self.active_connections < self._connection_limit
        if admitted:
            self.active_connections += 1
        token = self._admitted.set(admitted)
        try:
            yield
        finally:
            self._admitted.reset(token)
            if admitted:
                self.active_connections -= 1

    async def before_accept(self, handshake: RpcHandshake) -> None:
        if not self._admitted.get():
            raise RpcReject(RpcRejection.UNAVAILABLE, "Connection capacity reached")

    @asynccontextmanager
    async def request(self, scope: RpcRequestScope) -> AsyncGenerator[None, None]:
        async with self._requests:
            self.active_requests += 1
            try:
                yield
            finally:
                self.active_requests -= 1
```

Register that same instance as middleware and handshake hook:

```python
capacity = SharedCapacity(connections=200, requests=64)
router.include_channel(
    tasks,
    path="/rpc",
    middleware=[capacity],
    limits=RpcLimits(max_pending_requests=128),
    before_accept=capacity.before_accept,
)
```

Custom transports using `serve()` also run the registered handshake hook.
Alternatively pass `before_accept=capacity.before_accept` to an endpoint's `serve()`.
The shared request semaphore limits requests across sockets, while per-connection
admission bounds requests waiting for that semaphore. Both scopes use `finally`,
so handler failures, rejected handshakes, disconnects, and cancellation release
their permits. Long-lived message streams and custom raw handlers need
their own application limits; the request scope covers stream control
calls, not the entire stream lifetime.

This is a worker-local bound for one asyncio event loop. Multiple workers each
own independent counters; use deployment connection limits or coordinated
accounting when the required bound spans workers or hosts.

## Use Pydantic directly

`RpcModel` is fastapi_canon.integrations.websockets's strict Pydantic base model. Existing Pydantic
`BaseModel` classes are supported too, so domain models do not need to inherit
from `RpcModel` merely to appear in an RPC signature.
Plain models retain their declared field names, validation/serialization aliases,
nested model configuration, validators and extra-field policy. RPC does not clone
them or install its own alias generator. For example, `project_id: int` stays
`project_id` on the wire unless the model declares an alias.

When migrating from automatic adaptation, use `RpcModel` to retain camel-case
names and rejection of unknown fields, or configure that policy explicitly on
the Pydantic model. Regenerate clients after choosing the wire contract. See
[shared DTOs](../../unified-canon-concept.md#names-and-shared-models).

[Back to documentation](README.md)
## Idempotent methods

Declare `@channel.method(idempotent=True)` only when concurrent or repeated
execution with the same params has the same intended effect. The contract exports
`x-rpc-idempotent: true`; generated route metadata carries the flag. Omitted or
false declarations leave methods non-idempotent. This is a service guarantee:
fastapi_canon.integrations.websockets does not deduplicate requests or provide idempotency keys. Read operations
often qualify; writes require careful service-level design.
