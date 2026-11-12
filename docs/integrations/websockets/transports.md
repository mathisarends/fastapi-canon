# Transports

FastAPI is the supported application entry point for WebSocket RPC. The runtime's
`RpcWebSocket` seam remains available for isolated tests and existing adapters; it is
not a promise to maintain other framework integrations.

## Message size and transport buffering

`RpcLimits.max_message_bytes` defaults to 1 MiB and is checked after
`RpcWebSocket.receive()` returns an assembled message. Configure the transport's
message-size limit too, so oversized input is rejected before the application
buffers it. For Uvicorn with its explicit `websockets` backend:

```bash
uvicorn my_api:web --ws websockets --ws-max-size 1048576 --ws-max-queue 4
```

The Python equivalents are `uvicorn.run(..., ws="websockets",
ws_max_size=1_048_576, ws_max_queue=4)`. Use the same value in
`RpcLimits(max_message_bytes=1_048_576)`. The message limit applies to the combined
payload of a fragmented WebSocket message, not independently to each fragment.
The transport may close oversized messages with WebSocket code 1009 before fastapi_canon.integrations.websockets
can produce a JSON-RPC response.

Pin and check the backend you deploy rather than relying on `--ws auto`.
Uvicorn's `--ws-max-queue` applies to the `websockets` backend. For example, in
Uvicorn 0.35.0 `websockets-sansio` uses `ws_max_size` but does not use
`ws_max_queue`, while `wsproto` does not use either setting. Newer versions may
differ; check the [Uvicorn settings](https://uvicorn.dev/settings/) and the
backend implementation for your installed version.
The verified `websockets` mapping is in
[Uvicorn 0.35.0](https://github.com/Kludex/uvicorn/blob/0.35.0/uvicorn/protocols/websockets/websockets_impl.py).

Incoming transport queues, assembled messages, decoded JSON, and fastapi_canon.integrations.websockets's own
pending items and outgoing queue all consume memory. A message-size limit alone
does not bound their sum. The legacy `websockets` backend queues complete
messages; text can require up to four bytes per character in Python, so four
queued 1 MiB messages can take roughly 16 MiB before accounting for other buffers
or objects. See [websockets memory usage](https://websockets.readthedocs.io/en/10.0/topics/memory.html).
Tune queue lengths alongside message size and connection limits, and verify
oversized fragmented messages against the actual backend during deployment.

## FastAPI

Mount JSON-RPC channels on the feature's `APIRouter` and contribute it through
`Feature(routers=[router])`:

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature, export_contracts
from fastapi_canon.contracts import ContractKind

router = APIRouter()
router.include_channel(channel, path="/rpc")
router.include_channel(projects, path="/projects/rpc")
app = Composition(
    features=(Feature(name="control", routers=[router]),),
).build(FastAPI(title="Control"))
contract = export_contracts(
    app, kinds=(ContractKind.OPENRPC,), base_url="wss://api.example.com"
).openrpc
```

Every mounted path appears in one OpenRPC contract. Declare channel operations
before mounting; each call takes a snapshot. RPC limits, middleware, error
mapping and handshake rejection options are passed to `include_channel` and
configure that endpoint. A router `prefix=` is included in endpoint paths and the
contract. Binary streams and raw sockets are not JSON-RPC channels; declare them
on their own `CanonWebSocketRouter` as described in [streams](streams.md) and
[sockets](sockets.md).

### Mount endpoints with FastAPI dependencies

Pass an async dependency as `context=`. Its concrete return annotation supplies
the type injected into handlers as `Inject[T]`. The dependency can receive
path parameters, FastAPI dependencies and Dishka services.

```python
async def open_job(job_id: str) -> Job:
    return await jobs.require(job_id)


router.include_channel(
    job_events,
    path="/jobs/{job_id}/rpc",
    context=open_job,
    rejects=[job_not_found],
)
```

`Composition` installs and closes the shared Dishka container that resolves
`Inject[T]` parameters and the context's own Dishka services. The context is resolved before acceptance. Declared `rejects=` and
`rejections=` map its failures to HTTP denial responses when the ASGI server
supports that extension. Otherwise the server returns its generic 403 denial.

FastAPI dependencies passed as `include_channel(..., dependencies=[...])` can also
perform admission checks.
Their return values reach RPC handlers through the configured context or resolver.

### Mount a custom connection handler

A custom FastAPI handler is a protocol-level endpoint: declare it with `serve=`
on a `CanonWebSocketRouter` socket. It can declare `Inject[T]` arguments for the
context and resolver dependencies.
The same connection middleware and rejection policies wrap the handler.

```python
async def custom(websocket: WebSocket, job: Inject[Job]) -> None:
    await websocket.accept()
    await websocket.send_text(job.id)
    await websocket.close()


rpc = CanonWebSocketRouter(context=open_job)
rpc.socket("/jobs/{job_id}/custom", channels=[job_events], serve=custom)
```

## Custom adapters

Implement `RpcWebSocket` and pass it to `app.serve(socket)`. The protocol consists
of a handshake property and six async operations:

```python
from fastapi_canon.integrations.websockets import RpcHandshake


class MySocket:
    @property
    def handshake(self) -> RpcHandshake: ...

    async def accept(self, subprotocol: str | None = None) -> None: ...
    async def reject(self, rejection, reason: str) -> None: ...
    async def receive(self) -> str | bytes: ...
    async def send(self, message: str) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def close(self, close, reason: str) -> None: ...
```

`RpcHandshake` carries the request path, headers, query parameters, path
parameters, offered subprotocols, and optional client address. Signal client
disconnects by raising `RpcDisconnect` from `receive()` or a send operation.

`app.serve()` matches the handshake path to the declared endpoint. Pass
`root_path=` when an upstream server has already consumed a URL prefix.

For low-level dispatch tests that do not need connection behavior, obtain an
`RpcServer` from `endpoint.create_server(...)` or `channel.create_server(...)` and call
`handle()` or `handle_json()` directly.

## In-memory testing and directional faults

`fastapi_canon.integrations.websockets.testing.InMemorySocket` implements the server adapter. Its `client` view
(`InMemoryClientSocket`) provides `send()` / `recv()` / `close()` for generated
Python WebSocket clients, using the same incoming and outgoing queues. Both views
preserve close code and reason; the client exposes `close_code` and `close_reason`.
`RpcTestClient` remains the convenience client for ordinary service tests.

### Test the application router

In Canon 0.18.0 and later, pass your public router or composed FastAPI
application directly to `RpcTestClient` or `RpcTestStream`. Connections run through
the ASGI routes, including authentication, hooks, middleware and Dishka scopes.

```python
from fastapi_canon import APIRouter
from fastapi_canon.integrations.websockets.testing import RpcTestClient

router = APIRouter(prefix="/api")
channel = router.channel("/rpc", namespace="system")


@channel.method
async def ping() -> str:
    return "pong"


async def check_ping() -> None:
    async with RpcTestClient(router, "/api/rpc") as rpc:
        assert await rpc.request("system.ping") == "pong"
```

Configure dependencies, context and connection policies on the application.
The client's `resolver`, `context`, `error_mapper`, `limits` and `rejections`
overrides are reserved for specialized in-memory routers and are rejected for
ASGI targets. Headers, query strings and subprotocols use the real admission path.
Binary streams and typed/raw Canon sockets retain `next_frame`, `send_frame`,
`next_message` and `send_message`.

The caller owns application startup and shutdown. For a composed app, place the
client inside `async with app.router.lifespan_context(app):`; share that lifespan
when testing several connections. Router-only tests use a temporary FastAPI app
without automatically installing a Composition or a DI container. Unhandled ASGI
exceptions propagate on context exit, and disconnect closes the connection's
resources. This in-process transport does not establish network or browser behavior.

Each socket has independent `client_to_server` and `server_to_client`
`SocketFaults` controllers. Pass them to the constructor or configure the existing
controllers before sending frames:

```python
from fastapi_canon.integrations.websockets.testing import InMemorySocket, SocketFaults

socket = InMemorySocket(
    "/rpc",
    client_to_server=SocketFaults(drop_after=2, close_code=1011, reason="test drop"),
)
socket.server_to_client.hold()
```

- `drop_after=N` disconnects after N send attempts finish; N=0 drops before the
  first frame. Already queued frames precede the disconnect. `drop_when(frame)`
  drops before delivering a matching frame. Held frames are discarded on drop.
- `close_code=1006` (the default for injected drops) represents an abrupt
  disconnect without a close frame. It is reported as abnormal closure, never
  passed to a server adapter as a wire close code. Set `reason` to preserve a
  diagnostic or supply a regular close code such as 1008 or 1011.
- `delay=seconds` or `delay=lambda frame: seconds` delays a directional send.
  Inject `sleep=clock.sleep` to use a controllable test clock; its awaitable must
  honor cancellation. No clock-dependent test needs a wall-clock sleep.
- `hold()` buffers frames while allowing send to complete. Await `held.wait()`
  and inspect `held_count`; `release()` delivers them in insertion order.
- `half_open=True` silently discards frames in that direction without closing
  the socket. The opposite direction stays usable.
- `block()` simulates a slow consumer by blocking send itself. Await
  `blocked.wait()` to synchronize the test, then `unblock()` to release it.
  Caller cancellation propagates and does not deliver the cancelled frame.

Use one controller per connection direction. Disconnect releases blocked sends
with `RpcDisconnect`, cancels delay waits, and clears held frames. Controllers
own no background workers; temporary wait tasks are awaited during cleanup.

`InMemorySocketFactory` runs a service for each generated-client connection and
owns cleanup through an async context manager. Its `configure(socket, attempt)`
callback can script different failures for successive connections. `attempts`
includes rejected attempts, and `sockets` records connections actually created:

```python
from fastapi_canon.integrations.websockets.testing import (
    InMemorySocketFactory,
    RpcTestHandshakeRejected,
)


def configure(socket, attempt):
    socket.server_to_client.hold()


async with InMemorySocketFactory(app, configure=configure) as factory:
    factory.reject_next(status=503, reason="temporary outage")
    try:
        await factory("ws://test/rpc")
    except RpcTestHandshakeRejected as error:
        assert error.status_code == 503

    async with TasksClient.connect(socket_factory=factory) as client:
        # Start a call, await the controller's held event, then release its answer.
        ...
```

`reject_next(close_code=1008, reason="policy")` scripts a rejection with close
metadata instead of an HTTP status. Rejections raise `RpcTestHandshakeRejected`
with `status_code`, `close_code`, and `reason`. Actual endpoint handshake
rejections are preserved too. The factory forwards URL paths, headers, and
subprotocols and accepts `resolver`, `context`, and `limits` for serving. Exiting
the factory disconnects every socket and awaits all service tasks; cancellation
during connection setup also cleans up the unfinished handshake.

See the late-response regression in
[`tests/unit/integrations/websockets/codegen/test_python_runtime.py`](../../../tests/unit/integrations/websockets/codegen/test_python_runtime.py)
for a complete generated-client example using held responses.

## Measure request throughput

Measure the in-process request path over `InMemorySocket` with:

```console
uv run --locked python scripts/websockets/bench_rpc_requests.py
```

It reports sequential round-trip latency and pipelined requests per second for
`RpcModel` and plain Pydantic params, list results, and small and 16 KiB payloads,
each the median of `--repeat` runs. Add `--profile` for the top cProfile entries
of the pipelined path. It covers frame decoding, validation, dispatch,
serialization and queueing, but excludes the ASGI server, WebSocket framing and
network. Compare runs on the same idle machine.

[Back to documentation](README.md)

# Application heartbeats

The RPC socket runtime reserves `$/ping` alongside `$/cancelRequest`. A ping
request has no params or `{}` and returns `null`. Malformed requests receive
`invalid_request`; notifications get no response. Batch responses preserve batch
shape. Control processing is bounded by frame/batch limits and the bounded
outgoing queue with its send timeout. Pings bypass request admission, handler
semaphores and request middleware, so handler overload does not make
an otherwise responsive connection appear dead.

Configure protocol ping settings in your WebSocket server separately. Those
pings let the server detect lost peers; browsers cannot originate WebSocket ping
frames, so client-side application heartbeats provide the reverse check.
## Drain before stopping an ASGI server

Trigger drain while the server still handles traffic, then stop the process.
For example, your deployment controller can call an authenticated internal
pre-stop endpoint and wait for completion before sending the termination signal:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi_canon import APIRouter
from fastapi_canon.integrations.websockets import RpcDrain

drain = RpcDrain()
router = APIRouter()
router.include_channel(tasks, path="/rpc", drain=drain)


@asynccontextmanager
async def lifespan(web):
    yield
    await drain.drain(timeout=0)  # Final cleanup if pre-stop already drained.


web = FastAPI(lifespan=lifespan)
web.include_router(router)


@web.post("/internal/drain")  # Protect this with your deployment authentication.
async def prepare_stop():
    await drain.drain(timeout=30)
    return {"drained": True}
```

The pre-stop call must run outside an RPC handler being drained. Allow the
deployment controller at least the drain budget plus the process shutdown budget.
`--timeout-graceful-shutdown` limits Uvicorn's subsequent task shutdown; it does
not schedule this application drain. A signal sent before the pre-stop call
finishes can still interrupt work.

[Uvicorn 0.54.0 shutdown ordering](https://github.com/Kludex/uvicorn/blob/0.54.0/uvicorn/server.py#L257)
requests connection shutdown and waits/cancels tasks before sending the lifespan
shutdown event. Its [graceful shutdown settings](https://uvicorn.dev/settings/#timeouts)
therefore cannot make teardown after lifespan `yield` into a pre-close hook.
The repository's real TCP smoke test uses Uvicorn's `websockets-sansio` backend,
drains through HTTP while a WebSocket call is in flight, receives the result and
1012 close, then stops Uvicorn and verifies lifespan cleanup. This verifies the
deployment ordering beyond a lifespan-only unit test. The FastAPI routes use the router's drain coordinator.
