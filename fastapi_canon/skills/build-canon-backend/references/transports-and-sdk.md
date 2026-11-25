# Transports, SDKs and observability

Read only the section relevant to the task, then its linked repository guide.
All integrations belong to the `fastapi-canon` distribution; extras select
dependencies. Keep core imports usable without optional runtimes.

## Shared feature router

Add HTTP, SSE and WebSocket declarations to the feature's existing `APIRouter`,
then contribute it once through `Feature`. WebRTC uses a separate `WebRtcRouter`
in that feature:

```python
from collections.abc import AsyncIterator

from fastapi.sse import EventSourceResponse

from fastapi_canon import APIRouter, Inject
from fastapi_canon.integrations.websockets import RpcChannel

from backend.features.projects.application import ProjectService

from .errors import PROJECT_MISSING
from .schemas import Project, ProjectEvent

router = APIRouter(namespace="projects", prefix="/projects")
channel = RpcChannel(namespace="projects")


@router.get("/events", response_class=EventSourceResponse)
async def events(
    projects: Inject[ProjectService],
) -> AsyncIterator[ProjectEvent]:
    async for event in projects.events():
        yield event


@channel.method("rename", raises=[PROJECT_MISSING])
async def rename(
    project_id: int, title: str, projects: Inject[ProjectService]
) -> Project:
    return await projects.rename(project_id, title)


router.include_channel(channel, path="/rpc")
```

The stream exports as `projects.events`. Router `namespace=` names HTTP/SSE
operations; RPC names still come from `RpcChannel(namespace="projects")` and its declared
methods. Like FastAPI bodies, one Pydantic model parameter is the whole params
object; otherwise named parameters form it. Use `Annotated[T, Path()]` for path
variables of the mounted endpoint. Follow the shared [operation naming rules](http-feature.md#operation-ids),
including explicit overrides and collision handling.

Declare channel operations before inclusion: each mount captures a snapshot.
Use a `WebRtcRouter` with `@rtc.session(...)` for managed media sessions after
selecting the required engine and session options; add it to `Feature(routers=[...])`. See
[routers](https://github.com/mathisarends/fastapi-canon/blob/main/docs/20-routers.md)
for full options; the protocols retain their own admission, scope and cleanup rules.

## Typed SSE

Install the `sse` extra. Declare streams with
`@router.get(..., response_class=EventSourceResponse)`; a typed async iterator supplies the business event model. The generator requests services
with `Inject[T]`; they are resolved on its first iteration, after response start.

| Failure phase | Declaration and behavior |
| --- | --- |
| Before response start | Auth/lookup in real FastAPI dependencies; `raises=[...]`; HTTP Problem Details |
| During generator execution | The same `raises=[...]`; terminal `canon.problem` |
| Normal exhaustion | `canon.complete` |
| Cancellation / broken connection | Cleanup and transport/cancellation semantics |

`raises` automatically registers the declared errors. Even code before the first
`yield` executes after response start. Use dependencies
for admission checks. For data access during iteration, inject an APP-owned short
scope factory as described in [resources](resources.md).

Generated iterators yield business events, consume completion and raise shared
`ProblemError` for terminal problems; unexpected EOF is a transport failure. See [streams.md](https://github.com/mathisarends/fastapi-canon/blob/main/docs/03-streaming.md) and
[integrations/sse/01-clients.md](https://github.com/mathisarends/fastapi-canon/blob/main/docs/integrations/sse/01-clients.md) for complete declarations and client behavior.

## WebSocket RPC

Install the `websockets` extra. Declare typed methods and events on an
`RpcChannel` with `@channel.method(raises=[...])`, using the same public `Error`
definitions as HTTP, and mount it with `router.include_channel(channel,
path="/rpc")` after declaring operations. Methods request services with
`Inject[T]`, resolved from Composition's Dishka container, as in HTTP and SSE
endpoints. Handshake authentication uses `context=`/`hooks=` and
`rejects=` on the same call; see
[integrations/websockets/06-dependencies.md](https://github.com/mathisarends/fastapi-canon/blob/main/docs/integrations/websockets/06-dependencies.md).

Install the `codegen` extra for contract export and client generation.
Export the composed app with `fastapi-canon contracts export --app
backend.app:create_app --output-dir contracts` so effective paths and problem
types match runtime. JSON-RPC problem data lives inside its protocol envelope.
Binary streams and sockets are not JSON-RPC channels; they keep their own
endpoints, framing and close rules. Mount them with
`@router.binary_stream(path, ...)` and `@router.socket(path, ...)`. See
[integrations/websockets/01-services.md](https://github.com/mathisarends/fastapi-canon/blob/main/docs/integrations/websockets/01-services.md) and
[problems.md](https://github.com/mathisarends/fastapi-canon/blob/main/docs/02-errors.md).

## WebRTC

Install the `webrtc` extra and read
[webrtc.md](https://github.com/mathisarends/fastapi-canon/blob/main/docs/11-webrtc.md)
before choosing an engine for `WebRtcRouter(engine=...)`. Media and session lifetimes
need explicit ownership; an SSE convention is not a WebRTC or binary-socket
protocol.

## One workflow for separate clients

Use router namespaces and endpoint names to obtain operations such as
`projects.get` and `projects.events`, following
[operation naming](http-feature.md#operation-ids). Feature names and folder paths
do not define SDK namespaces. Preserve existing public operation IDs with
`operation_id=` when refactoring; use explicit generation mappings when adapting
an existing naming contract.
Reuse server Pydantic models and `Error` definitions. Generated client types may
be repeated across transport outputs. Use the version-2 workflow documented in
[codegen.md](https://github.com/mathisarends/fastapi-canon/blob/main/docs/06-codegen.md).
Configure specialized SSE/RPC/WebRTC targets and explicit external HTTP commands.
HTTP runtime and query/mutation helpers belong to established OpenAPI tooling.
Run `fastapi-canon generate --check --diff` after changing contract sources.
Do not recreate the removed HTTP generator, shared model registry or domain facade.


## Telemetry and request logging

Enable `Composition(features=features, request_logging=True)` for structured HTTP
completion/abort records. The application configures logging outputs; Canon does
not configure process-wide logging. See [request-logging.md](https://github.com/mathisarends/fastapi-canon/blob/main/docs/10-observability.md).

Telemetry is opt-in at composition. Import `Telemetry` and `instrument` from
`fastapi_canon.integrations.opentelemetry`; declare
`Feature(..., integrations=[instrument(ProjectRepository, ProjectService)])` and
enable `Composition(integrations=[Telemetry(...)])`. Pass application-owned providers to `Telemetry` as documented in
[telemetry.md](https://github.com/mathisarends/fastapi-canon/blob/main/docs/10-observability.md). Instrument declarations leave ordinary Dishka providers
usable on their own. Enabled telemetry automatically instruments Dishka-provided
clients and engines through installed resource adapters; `resources=[]` opts out.
Explicit resource contexts remain available for per-instance settings. Use
`Telemetry.disabled()` when retaining instrumentation parts or injected telemetry
configuration without recording signals. The application owns exporter/provider shutdown.
