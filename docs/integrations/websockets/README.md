# WebSocket APIs

Use a channel to group typed RPC methods and message streams under a namespace.
Mount it on Canon's `APIRouter` alongside HTTP routes and SSE streams.
Install `fastapi-canon[websockets]`; add `codegen` to generate clients.

```python
from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_canon import APIRouter, Composition, Feature
from fastapi_canon.integrations.websockets import RpcChannel


class Greeting(BaseModel):
    name: str


hello = RpcChannel("hello")


@hello.method()
async def greet(params: Greeting) -> str:
    return f"Hello, {params.name}"


router = APIRouter()
router.include_channel(hello, path="/ws")
app = Composition(
    features=(Feature(name="hello", routers=[router]),),
).build(FastAPI())
```

The public operation is `hello.greet`. Parameters and return annotations drive
the contract. Add `raises=[public_error]` for declared domain failures; request
application services through `Inject[T]` and feature-owned Dishka providers.

The [quickstart](../../quickstart.md) combines an RPC method with HTTP and SSE,
then generates one Python and TypeScript SDK. Clients handle request IDs,
serialization, correlation and connection cleanup.

## Choose the interaction

| Need | Use |
| --- | --- |
| One typed result | [Channel method](services.md) |
| A sequence of typed results | [Message stream](message-streams.md) |
| Push notifications and connection hooks | [Connections and events](connections-and-events.md) |
| Upload or download bytes | [Binary stream](streams.md) |
| An existing WebSocket protocol | [Typed or raw socket](sockets.md) |

Message streams support explicit completion, failure and cancellation. A stream's
cleanup ends that delivery; cancelling an independently running business job
requires an application operation. Use managed stream resources when leaving
early, and close owned clients on shutdown.

## Application concerns

- [Dependencies and authentication](dependencies.md)
- [Public errors](errors.md)
- [Generated clients](clients.md) and [generation workflow](../../codegen.md)
- [Telemetry](observability.md)
- [Upgrade guide](migration.md) and [resilience options](hardening-overview.md)

For custom implementations, consult the [wire protocol](protocol.md) and
[transport adapters](transports.md). These references are optional for normal
application development.
