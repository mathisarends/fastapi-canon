# Migrate WebSocket applications to Canon

## Canon 0.13.0 server composition

`CanonWebSocketRouter` is the public WebSocket composition API. Replace the separate
service, adapter factory and mounting objects with a router.
If using an earlier development snapshot, replace `RpcRouter` with
`CanonWebSocketRouter`. Replace `Telemetry.rpc_middleware()` with
`Telemetry.websocket_middleware()`. Neither old name has a compatibility alias.

```python
from fastapi_canon.integrations.websockets import RpcChannel, CanonWebSocketRouter
from fastapi_canon.integrations.websockets.adapters.dishka import Dishka

rpc = CanonWebSocketRouter("/rpc", channels=[tasks], resolver=Dishka())
feature = Feature(name="tasks", routers=[rpc], providers=[TaskProvider])
app = Composition(features=[feature]).build(FastAPI())
contract = rpc.contract(title="Tasks", base_url="wss://api.example.com", app=app)
```

For several endpoints, use `CanonWebSocketRouter()` and its `socket()` and `stream()`
methods. They register and mount each endpoint together. Configure limits,
middleware, rejection policies and the async connection-context dependency on
the router. Its dependency's concrete return type replaces the separate
endpoint context-type declaration. Custom FastAPI handlers use `serve=`.

`RpcService`, `RpcRoutes`, `create_router` and `dishka_router` are removed.
The transport-neutral definition is an implementation detail. Use the router
with `RpcTestClient` or call `router.endpoint(name).create_server()` for tests.
The existing wire contracts and SDKs continue to use the declared channels.

The implementation ships in the unified Canon distribution. The original
pyrpckit repository and the earlier standalone `fastapi-rpckit` release records
remain available; current installs use Canon extras.

| Before | After |
| --- | --- |
| distribution `pyrpckit` or `fastapi-rpckit` | `fastapi-canon[websockets]` |
| Python import `rpckit` or `fastapi_rpckit` | `fastapi_canon.integrations.websockets` |
| CLI `rpckit` or `fastapi-rpckit` | `fastapi-canon websockets` |
| optional `[fastapi]` extra | FastAPI is required; remove the extra |
| logger `rpckit` | logger `fastapi_canon.integrations.websockets` |

Install with `uv add "fastapi-canon[websockets]"`; add the `codegen` and `otel`
extras where needed. Dishka is a base dependency, with no `dishka` extra.
Update imports throughout application code, codegen source modules, monkeypatch
targets, and configuration.

The FastAPI transport APIs are directly available:

```python
from fastapi_canon.integrations.websockets import (
    CanonWebSocketRouter,
    FastApiSocket,
    serve_websocket,
)
```

The existing `fastapi_canon.integrations.websockets.adapters.fastapi` paths also remain valid.
`RpcChannel`, endpoint registration, and `Inject` retain their behavior. With Canon,
put `CanonWebSocketRouter(..., resolver=Dishka())` in a feature's routers; it uses
Canon's application container, SESSION per connection, and REQUEST per call.
See the [workspace example](../../../examples/stack_service.py).

For telemetry, WebSocket calls without message metadata now start separate traces
and link to the available connection context. Explicit message metadata continues
the caller's trace. Handler child spans and standalone channel-server parenting
are unchanged. No client changes are needed for server-side controller tracing.

The JSON-RPC envelopes, method names, control frames, OpenRPC `x-rpckit-*`
extensions, binary/raw framing, and `.rpcgen` manifest format are deliberately
preserved, as are the `rpckit.*` OpenTelemetry metric and attribute names so
existing dashboards continue to work. Generated clients do not require server-package imports.
Regenerating clients updates their generator attribution; use the same
`rpcgen.toml` and the renamed CLI, then run `--check` and consumer tests.

No `rpckit` Python compatibility shim is installed. The old and new distributions
can coexist during application migration because their import namespaces differ.
Existing generic adapter seams are retained for tests and migration, while the
supported product experience is FastAPI WebSockets.

For consumers still on pyrpckit 0.8 or earlier, also apply the predecessor's
0.9–0.11 changelog migrations. In particular, `rpckit.fastapi` becomes the root
FastAPI exports or `fastapi_canon.integrations.websockets.adapters.fastapi`, and `rpckit.dishka`
becomes `fastapi_canon.integrations.websockets.adapters.dishka`. Simply replacing the namespace is
insufficient for those removed modules. Observer/hooks and custom-transport
changes also require call-site updates where used.

The [workspace parity report](../../rpc-parity.md) compares against the
local pyrpckit 0.11.0 checkout and records a real Cara contract/SDK check. Cara's
full application migration is still pending. Python SDK generation now wraps
long enum defaults, event unions and route annotations for Ruff compatibility;
regenerate SDKs to receive that formatting fix.

The initial local successor name was `fastapi-wsrpc` / `fastapi_wsrpc`. PyPI
rejected its pending publisher registration as too similar to an existing project.
On 2026-10-17 the successor used `fastapi-rpckit` / `fastapi_rpckit`, and PyPI
accepted the new Pending Publisher for `mathisarends/canon`, `release.yml`,
environment `pypi`. Update imports, CLI commands and codegen references
if migrating from the earlier, unpublished workspace name.
