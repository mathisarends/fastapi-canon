# WebSocket APIs

Use a channel to group typed RPC methods and message streams under a namespace.
Mount it on Canon's `APIRouter` alongside HTTP routes and SSE streams.
Install `fastapi-canon[websockets]`; add `codegen` to generate clients.

```python
from fastapi import FastAPI

from fastapi_canon import APIRouter, Composition, Feature
from fastapi_canon.integrations.websockets import RpcChannel

hello = RpcChannel(namespace="hello")


@hello.method()
async def greet(name: str) -> str:
    return f"Hello, {name}"


router = APIRouter()
router.include_channel(hello, path="/ws")
app = Composition(
    features=(Feature(name="hello", routers=[router]),),
).build(FastAPI())
```

The public operation is `hello.greet`, called with `{"name": "Ada"}`. As in
FastAPI, parameters and return annotations drive the contract: named parameters
form `params`, a single Pydantic model parameter is the whole `params` object,
and `Annotated[T, Path()]` reads a URL variable. Add `raises=[public_error]` for declared domain failures; request
application services through `Inject[T]` and feature-owned Dishka providers.

The [quickstart](01-quickstart.md) combines an RPC method with HTTP and SSE,
then generates one Python and TypeScript SDK. Clients handle request IDs,
serialization, correlation and connection cleanup.

## Choose the interaction

| Need | Use |
| --- | --- |
| One typed result | [Channel method](integrations/websockets/services.md) |
| A sequence of typed results | [Message stream](integrations/websockets/message-streams.md) |
| Push notifications and connection hooks | [Connections and events](integrations/websockets/connections-and-events.md) |
| Upload or download bytes | [Binary stream](integrations/websockets/streams.md) |
| An existing WebSocket protocol | [Typed or raw socket](integrations/websockets/sockets.md) |

Message streams support explicit completion, failure and cancellation. A stream's
cleanup ends that delivery; cancelling an independently running business job
requires an application operation. Use managed stream resources when leaving
early, and close owned clients on shutdown.

## Application concerns

- [Dependencies and authentication](integrations/websockets/dependencies.md)
- [Public errors](integrations/websockets/errors.md)
- [Generated clients](integrations/websockets/clients.md) and [generation workflow](06-codegen.md)
- [Telemetry](integrations/websockets/observability.md)
- [Upgrade guide](integrations/websockets/migration.md) and [resilience options](integrations/websockets/hardening-overview.md)

For custom implementations, consult the [wire protocol](integrations/websockets/protocol.md) and
[transport adapters](integrations/websockets/transports.md). These references are optional for normal
application development.

---

Previous: [Streaming](03-streaming.md) · [Learning path](README.md) · Next: [Application composition](05-composition.md)
