# Transports and SDKs

Every transport sits on the feature's existing `APIRouter` and reuses the same
services and `Error` definitions. Install only the extras you use.

## SSE (`sse` extra)

```python
from collections.abc import AsyncIterator

from fastapi.sse import EventSourceResponse

from fastapi_canon import APIRouter, Inject

router = APIRouter(namespace="projects", prefix="/projects")


@router.get("/events", response_class=EventSourceResponse, raises=[PROJECT_MISSING])
async def events(projects: Inject[ProjectService]) -> AsyncIterator[ProjectEvent]:
    async for event in projects.events():
        yield event
```

Exported as `projects.events`. The generator body, even before the first
`yield`, runs after the response starts:

| Phase | Behavior |
| --- | --- |
| Admission (auth, lookups in FastAPI dependencies) | HTTP Problem Details |
| Error raised while iterating | Terminal `canon.problem` event |
| Normal end | `canon.complete` |
| Disconnect | Cancellation and cleanup |

Streams may inject only APP-scoped services; REQUEST resources close before
streaming (`canon.providers.E003`). For database access while iterating, inject
a `Scoped` factory ([resources.md](resources.md#background-work-and-streams)).

## WebSocket RPC (`websockets` extra)

```python
from fastapi_canon.integrations.websockets import RpcChannel

channel = RpcChannel(namespace="projects")


@channel.method("rename", raises=[PROJECT_MISSING])
async def rename(
    project_id: int, title: str, projects: Inject[ProjectService]
) -> ProjectResponse:
    return await projects.rename(project_id, title)


router.include_channel(channel, path="/rpc")  # after declaring all methods
```

Params come from the signature: one Pydantic model parameter is the whole
params object, otherwise the named parameters form it. Use `BaseModel` for wire
models. Handshake auth: `context=`, `hooks=` and `rejects=` on
`include_channel`. Raw binary endpoints use `@router.binary_stream(...)` and
`@router.socket(...)`. See the
[WebSocket guide](https://github.com/mathisarends/fastapi-canon/blob/main/docs/04-websockets.md).

## WebRTC (`webrtc` extra)

Add a `WebRtcRouter` from `fastapi_canon.integrations.webrtc` with
`@rtc.session(...)` to `Feature(routers=[router, rtc])`. Read the
[WebRTC guide](https://github.com/mathisarends/fastapi-canon/blob/main/docs/11-webrtc.md)
before choosing an engine.

## MCP

`fastapi_canon.integrations.mcp` (`mcp` extra): declare tools on an
`McpServer`, mount it with
`Feature(integrations=[McpMount(server=..., namespace=...)])` and enable
`Composition(integrations=[Mcp(auth=...)])`. HTTP dependencies do not protect
MCP; see the [MCP guide](https://github.com/mathisarends/fastapi-canon/blob/main/docs/12-mcp.md).

## Generated clients (`codegen` extra)

Canon generates SSE, RPC (Python/TypeScript) and WebRTC (TypeScript) clients;
plain HTTP uses your OpenAPI tool on `contracts/openapi.json`.

```toml
# fastapi-canon.toml
[codegen]
version = 2
app = "backend.app:create_app"
contracts = "contracts"

[codegen.targets.streams]
generator = "sse"
language = "typescript"
output = "frontend/src/api/streams"
```

```console
uv run fastapi-canon generate                 # write contracts and clients
uv run fastapi-canon generate --check --diff  # CI drift check
```

Commit contracts and clients together. Details:
[client generation](https://github.com/mathisarends/fastapi-canon/blob/main/docs/06-codegen.md).
