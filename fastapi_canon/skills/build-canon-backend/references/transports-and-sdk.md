# Transports, SDKs and observability

Read only the section relevant to the task, then its linked repository guide.
All integrations belong to the `fastapi-canon` distribution; extras select
dependencies. Keep core imports usable without optional runtimes.

## Shared feature router

For installed versions exposing `fastapi_canon.APIRouter`, add declarations to
the feature's existing router, then contribute it once through `Feature`:

```python
from collections.abc import AsyncIterator

from fastapi_canon import APIRouter, RpcChannel

router = APIRouter(prefix="/projects")
channel = RpcChannel("projects")


@router.sse("/events", operation_id="projects.events")
async def project_events() -> AsyncIterator[str]:
    yield "ready"


@channel.method("ping")
async def ping() -> str:
    return "pong"


router.include_channel(channel, path="/rpc")
```

Declare channel operations before inclusion: each mount captures a snapshot.
Use `@router.webrtc(...)` for managed media sessions after selecting the required
engine and session options. Existing specialized routers remain valid. See
[routers](https://github.com/mathisarends/fastapi_canon/blob/main/docs/routers.md)
for full options; the protocols retain their own admission, scope and cleanup rules.

## Typed SSE

Install the `sse` extra. A typed async iterator supplies the business event model.
Use `@router.sse(...)` above, or `CanonStreamRouter` from
`fastapi_canon.integrations.sse` for an existing dedicated stream router.

| Failure phase | Declaration and behavior |
| --- | --- |
| Before response start | Auth/lookup in real FastAPI dependencies; `raises=[...]`; HTTP Problem Details |
| During generator execution | `stream_raises=[...]` from the same catalog; terminal `canon.problem` |
| Normal exhaustion | `canon.complete` |
| Cancellation / broken connection | Cleanup and transport/cancellation semantics |

Even code before the first `yield` executes after response start. Use dependencies
for admission checks. For data access during iteration, inject an APP-owned short
scope factory as described in [resources](resources.md).

Generated iterators yield business events, consume completion and raise shared
`ProblemError` for terminal problems; unexpected EOF is a transport failure.
`CanonResponse.sse()` documents a manual streaming response but does not provide
the typed Canon stream protocol. See [streams.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/streams.md) and
[integrations/sse/clients.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/integrations/sse/clients.md) for complete declarations and client behavior.

## WebSocket RPC

Install the `websockets` extra. Declare typed
methods on the channel with `@channel.method(raises=[...])`, using the same public
`Error` definitions as HTTP. On a dedicated `CanonWebSocketRouter` from
`fastapi_canon.integrations.websockets`, mount with
`rpc.socket("/rpc", channels=[channel])` after declaring operations, then
contribute the router to a `Feature`. For Dishka, follow [integrations/websockets/dependencies.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/integrations/websockets/dependencies.md);
HTTP's `DishkaRoute` is not the RPC injection mechanism.

Export the composed app with `fastapi-canon contracts export --app
backend.app:create_app --output-dir contracts` so effective paths and problem
types match runtime. Do not copy older `RpcService` wiring. JSON-RPC problem
data lives inside its protocol envelope. Raw/binary socket sessions retain their
own framing and close rules. See [integrations/websockets/services.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/integrations/websockets/services.md) and
[problems.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/problems.md).

For WebRTC, read [webrtc.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/webrtc.md) before choosing `CanonWebRtcRouter` and an engine.
Media/session lifetimes need explicit ownership; an SSE convention is not a
WebRTC or binary-socket protocol.

## One generated domain SDK

Choose stable explicit operation names such as `projects.get`. Feature names and
folder paths do not define SDK namespaces. Preserve existing public operation
IDs; use explicit generation mappings when adapting an existing naming contract.
Use shared semantic model identities for the same data across transports, rather
than assuming structurally similar models are the same type.

For the HTTP walkthrough, save this in the consumer's root `fastapi-canon.toml`:

```toml
[codegen]
version = 1

[codegen.sources.api]
kind = "openapi"
source = "backend.main:app"
output = "contracts/openapi.json"

[codegen.targets.python]
language = "python"
sources = ["api"]
output = "clients/python"
package = "projects_client"

[codegen.targets.browser]
language = "typescript"
sources = ["api"]
output = "frontend/generated"
```

Install the `codegen` extra as development tooling, then run:

```console
uv run fastapi-canon generate
uv run fastapi-canon generate --check --diff
```

Consumers call `client.projects.get(...)`. HTTP and typed SSE use the OpenAPI
source. Add an `openrpc` source that references an exported contract for RPC and
include it in the same target's `sources`. Where supported, an `openrpc` source
can also reference the app/factory directly. Use saved contracts and `--offline`
when generation must avoid importing the app.

Change declarations/templates, regenerate, and inspect drift. The generator owns
its manifest-listed files; do not hand-edit them or overwrite handwritten files.
Python and TypeScript consumers have their own runtime dependencies and do not
import Canon. Read [unified-canon-concept.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/unified-canon-concept.md) before adding mappings, changing
output ownership or supporting additional media. Unified HTTP generation covers
JSON and declared SSE; unsupported multipart/file operations need explicit
exclusion. Python WebRTC generation is unsupported.

## Telemetry and request logging

Enable `Composition(features=features, request_logging=True)` for structured HTTP
completion/abort records. The application configures logging outputs; Canon does
not configure process-wide logging. See [request-logging.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/request-logging.md).

Telemetry is opt-in at composition. Import `Telemetry` and `instrument` from
`fastapi_canon.integrations.opentelemetry`; declare
`Feature(..., instrumentation=instrument(ProjectRepository, ProjectService))`.
Pass application-owned providers to telemetry/composition as documented in
[telemetry.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/telemetry.md). Instrument declarations leave ordinary Dishka providers
usable on their own. Engine/client instrumentation uses the explicit resource
adapters, and the application owns exporter/provider shutdown.
