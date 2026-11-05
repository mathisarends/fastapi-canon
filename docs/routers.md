# One router for an application feature

Import `APIRouter` and `RpcChannel` from `fastapi_canon`. One application router
can contribute HTTP calls, typed SSE, JSON-RPC channels and WebRTC sessions:

| Declaration | Endpoint behavior |
| --- | --- |
| `@router.get(...)`, `post(...)`, etc. | HTTP with Canon error/success contracts |
| `@router.sse(...)` | Typed HTTP event stream, terminal problems and completion |
| `router.include_channel(channel, path=...)` | JSON-RPC methods and message streams over one WebSocket |
| `@router.websocket(...)` | Native FastAPI WebSocket handler and application protocol |
| `@router.webrtc(...)` | WebRTC session factory, signaling, admission and cleanup |

These additions are available in this checkout. Select the `sse`, `websockets`
or `webrtc` extra for the transports you use. `aiortc` selects the reference
WebRTC engine. Core router imports do not load these optional runtimes or SDK
generators. Existing specialized routers remain supported.

## Automatic HTTP and SSE operation names

Declare a stable namespace once per router. Canon derives an omitted
`operation_id` from the endpoint function name:

```python
from collections.abc import AsyncIterator

from fastapi_canon import APIRouter


router = APIRouter(namespace="projects", prefix="/projects")


@router.get("/{project_id}")
async def get(project_id: int) -> int:
    return project_id


@router.sse("/{project_id}/events")
async def events(project_id: int) -> AsyncIterator[int]:
    yield project_id
```

These operations export as `projects.get` and `projects.events`, producing
`client.projects.get(...)` and `client.projects.events(...)` in the common SDK.
The same rule applies to `CanonRouter` and `CanonStreamRouter`.

- An explicit `operation_id=` always wins and is never prefixed.
- Otherwise the ID is `<namespace>.<function_name>`, or just `<function_name>`
  when `namespace=None` (the default). Namespaces may be dotted public identifiers,
  such as `organization.projects`; empty or private segments are rejected.
- URL prefixes, HTTP methods, route `name=`, parameter names, model names and
  `Feature.name` do not contribute to automatic IDs. Included Canon routers keep
  their own naming policy; a parent namespace does not replace a child's.
  Native FastAPI routes with the default naming policy inherit the Canon parent's
  generator according to FastAPI's inclusion rules.
- A custom FastAPI `generate_unique_id_function=` replaces Canon's default rule.
  `APIRouter.sse()` uses the same generator as the owning router.

Renaming an endpoint function changes its automatic ID and generated SDK member.
Pin `operation_id="projects.events"` when that public name must survive a Python
refactor. Use explicit IDs for anonymous or wrapped callables without a stable
function name; normal wrappers using `functools.wraps` retain that name. Callable
objects without `__name__` use their class name.

Operation IDs must be unique across all schema-visible HTTP and SSE operations.
Canon never appends numbers or method/path suffixes to resolve a collision.
Composition preflight rejects duplicates before installing routes, handlers or
providers. This includes reused endpoints, routers mounted under multiple URL
prefixes, and a single declaration selecting multiple HTTP methods. Give distinct
operations separate declarations and explicit IDs or distinct router namespaces.

Check the same policy from the CLI:

```console
fastapi-canon check composition app:composition --format json
```

The command exits with status 1 for collisions and reports `canon.routing.E002`,
the duplicate ID, owning features, both effective method/path locations and a
hint to set distinct `operation_id` values. `--require-complete` exits with status
2 when dependent checks were skipped. `fastapi-canon generate` also rejects
duplicate IDs and SDK naming conflicts before writing contracts or clients,
including generation from saved OpenAPI documents.

## Compose channels before choosing their URL

Channels describe operations. The router decides where clients connect.
Each inclusion copies the current declarations; subsequent changes to the
source channel do not change its already included or mounted snapshots.

```python
from collections.abc import AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_canon import APIRouter, Composition, Feature, RpcChannel
from fastapi_canon.integrations.websockets import Inject, RpcConnection


class RenameProject(BaseModel):
    title: str


projects = RpcChannel()
tasks = RpcChannel()


@projects.method("rename")
async def rename_project(
    params: RenameProject, connection: Inject[RpcConnection]
) -> str:
    project_id = connection.path_params["project_id"]
    return f"{project_id}: {params.title}"


@tasks.method("create")
async def create_task() -> str:
    return "created"


session = RpcChannel()
session.include_channel(projects, namespace="projects")
session.include_channel(tasks, namespace="tasks")

router = APIRouter(prefix="/projects")
router.include_channel(session, path="/{project_id}/session")

app = Composition(features=[Feature(name="projects", routers=[router])]).build(
    FastAPI()
)
```

One connection to `/projects/42/session` provides `projects.rename` and
`tasks.create`. For example:

```json
{"jsonrpc":"2.0","id":1,"method":"projects.rename","params":{"title":"Updated"}}
```

RPC methods retain the existing typed signature: at most one Pydantic request
model and explicitly injected dependencies. URL values belong to the connection,
not the client's `params`. Use `Inject[RpcConnection]` for the path context,
or a typed connection context/path model using the existing RPC options.

`namespace=` on `channel.include_channel()` replaces the included channel's root
namespace; nested namespaces are retained. Anonymous channels have no automatic
wire prefix. Named channels keep their existing behavior. Error registries and
per-operation resolver scopes stay attached to their declarations. Duplicate
operation names, namespace conflicts and stream-control collisions fail explicitly.

Separate URLs create separate connections:

```python
router = APIRouter(prefix="/projects")
router.include_channel(projects, path="/{project_id}/session", namespace="projects")
router.include_channel(tasks, path="/{project_id}/tasks/session", namespace="tasks")
```

Call `include_channel()` multiple times to register multiple endpoints. Each
connection has its own limits, pending requests and message streams. Its
`context`, `resolver`, `middleware`, `limits` and other RPC options configure the
existing WebSocket adapter. `dependencies=` runs ordinary FastAPI dependencies
before acceptance. Native FastAPI router prefixes and dependency overrides apply.
When Composition installs Dishka providers, omitted `resolver=` automatically
uses the application's container for `Inject[T]` in methods, streams and context
hooks. APP, SESSION and REQUEST lifetimes retain their existing cleanup behavior.
An explicit resolver takes precedence.
Duplicate WebSocket paths on the application router are rejected before inclusion.
Endpoint names default to the complete local path, including the router prefix,
so separate URLs ending in `/session` export separately. Pass `name=` when a
contract needs an explicit stable endpoint name.

## Message streams on the same connection

The current RPC message-stream decorator is `channel.stream()`:

```python
@projects.stream("events")
async def project_events() -> AsyncIterator[str]:
    yield "updated"
```

Register the stream before including `projects` in `session`. The existing wire
controls are `projects.events.open` and `projects.events.cancel`, using `streamId`.
Normal completion and failures retain the existing terminal notifications.
Cancelling one stream leaves other streams and methods usable; disconnect cleans
up all connection-owned work. This adds no new wire protocol and requires no SDK
runtime changes. Binary transfers and raw socket protocols use their own endpoints;
they are not silently multiplexed into a JSON-RPC channel mount.

See [WebSocket streams](integrations/websockets/index.md) for controls, limits,
error mappings, dependency lifetimes and generated resource APIs. Operation names
and endpoint names must be unique across the application when exporting a common SDK.

## Typed SSE and WebRTC on the same router

With the relevant extras installed, extend the router above:

```python
from fastapi_canon.integrations.webrtc import Session
from fastapi_canon.integrations.webrtc.engines.aiortc import AiortcEngine


@router.sse("/{project_id}/events", operation_id="project_events")
async def project_events(project_id: str) -> AsyncIterator[str]:
    yield f"Updated {project_id}"


@router.webrtc("/{project_id}/media", engine=AiortcEngine())
async def project_media(project_id: str) -> Session:
    return Session()
```

Make these declarations before composing the application. SSE infers its item
schema and serialization from the return annotation. It sends `canon.complete`
on normal exhaustion and `canon.problem` for processing failures. Declare
both pre-response failures and terminal stream failures with `raises=`. Errors
are registered automatically; no separate registry entry is required.
Perform admission checks in dependencies, before generator execution starts.

`webrtc()` accepts the existing session options: `role`, `media`, `data_channels`,
`limits`, `max_sessions`, `dependencies` and `raises`. Engine, ICE-server, event-hook
and origin options are also accepted on the decorator; the factory may supply an
engine through its returned `Session`. Each declaration owns its endpoint limits
and cleanup. Yield dependencies remain open through session cleanup. Composition
includes the WebRTC shutdown lifespan with the application's dependency lifetime.

The protocols keep their distinct lifecycles: an RPC failure answers one call,
an SSE failure terminates that stream, and accepted WebRTC sessions use their own
signaling and media lifecycle. Their paths and contracts can all be exported with
[`export_contracts(app)`](contracts.md) without starting factories or opening peers.
