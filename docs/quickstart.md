# From Python to typed clients

Build one small Projects API with an HTTP lookup, an SSE event stream and a
WebSocket cancellation method. The example demonstrates shared contracts;
connect the operations to your own application services for real project work.

In your application project, install the dependencies:

```console
uv add "fastapi-canon[sse,websockets]"
uv add --dev "fastapi-canon[codegen]" uvicorn
```

Create `src/__init__.py` and `src/app.py`, then follow the steps below.
For the existing runnable version, see the
[Projects example](../examples/canonical_errors/README.md).

## Core API: routes, errors and composition

Save this as `src/app.py`. A domain exception stays ordinary Python; its public
mapping drives both the HTTP response and the exported operation contract. The
next sections add a stream and an RPC method to the same router; keep the
composition block at the end of the file and add them above it.

```python
from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_canon import APIRouter, Composition, Error, ErrorOptions, Feature


class ProjectMissing(Exception):
    pass


class Project(BaseModel):
    id: int


project_missing = Error(
    ProjectMissing,
    status=404,
    code="project_missing",
    title="Project not found",
    detail="Choose another project.",
)
router = APIRouter()


def require_project(project_id: int) -> None:
    if project_id == 0:
        raise ProjectMissing()


@router.get(
    "/projects/{project_id}",
    operation_id="projects.get",
    raises=[project_missing],
)
async def get_project(project_id: int) -> Project:
    require_project(project_id)
    return Project(id=project_id)


feature = Feature(name="projects", routers=[router])
composition = Composition(
    features=(feature,),
    errors=ErrorOptions(type_base="https://api.example.com/problems"),
)
app = composition.build(FastAPI())
```

`build` checks every operation contract before installing the feature, then
confirms the installed app compiles to valid OpenAPI.
`GET /projects/1` returns `{"id": 1}`. `GET /projects/0` returns a 404
`application/problem+json` response with the stable type
`https://api.example.com/problems/project_missing` and code `project_missing`.
The same problem is documented in OpenAPI. An explicit `operation_id` gives
codegen the public SDK name `projects.get`; the `Project` return annotation
supplies its typed result and validates the server's returned data.

A feature can also contribute Dishka `providers=`, a `lifespan=` and optional
`instrumentation=`. Composition owns installation and cleanup. Shared
`RouterPolicy` declarations reduce repeated authentication, dependencies and
error contracts. See the [core API guide](composition.md),
[router policies](composition.md#router-policies) and
[shared problem contracts](problems.md).

## Streams: typed events from server to SDK

Add a stream to the same router, reusing the error definition and lookup above:

```python
from collections.abc import AsyncIterator

from fastapi import Depends


class ProjectEvent(BaseModel):
    sequence: int


@router.sse(
    "/projects/{project_id}/events",
    operation_id="projects.events",
    dependencies=[Depends(require_project)],
    raises=[project_missing],
)
async def events(project_id: int) -> AsyncIterator[ProjectEvent]:
    yield ProjectEvent(sequence=1)
    if project_id == 2:
        raise ProjectMissing()
```

The event model becomes the generated iterator's item type.
`raises=` registers errors and describes both HTTP admission failures and terminal
stream failures. Before response start, Canon sends an HTTP problem; during
generator execution, it sends `canon.problem` and closes the stream. Put admission
checks in dependencies: generator bodies run after the response begins.

The generated SDK handles SSE framing and JSON decoding, yields business events,
consumes `canon.complete`, and raises `ProblemError` on `canon.problem`.
Unexpected EOF raises `TransportError`; malformed control frames raise
`ProtocolError`. Consumers write an ordinary `async for` loop and can distinguish
completion from a broken connection. No application-specific SSE parser or
second error mapping is needed.

See [Canon stream contracts](streams.md),
[stream clients](integrations/sse/clients.md) and
[terminal problem behavior](problems.md#what-goes-over-the-wire).

## WebSockets: RPC calls as domain methods

Add a WebSocket method using the same public error definition, and mount its
channel on the same router:

```python
from fastapi_canon.integrations.websockets import RpcChannel

projects = RpcChannel("projects")


class CancelProject(BaseModel):
    project_id: int


@projects.method(raises=[project_missing])
async def cancel(params: CancelProject) -> None:
    require_project(params.project_id)


router.include_channel(projects, path="/ws")
```

The composition block at the end of the file now installs HTTP, SSE and RPC from
one feature router. Install a server and run the application from the project root:

```console
uv add --dev uvicorn
uv run uvicorn src.app:app --reload
```

The contract describes `projects.cancel` and its typed parameters and failures.
Methods request application services with `Inject[T]`, resolved from the
composition's Dishka container. Codegen handles request IDs, serialization,
response correlation and typed problem errors; the consumer calls `client.projects.cancel(...)`. HTTP, SSE and RPC
share the SDK's public model/error runtime. Protocol bindings stay internal.

WebSockets also support notifications, message streams, typed sockets and binary
streams, with explicit session lifecycles. See [services and channels](integrations/websockets/services.md),
[generated clients](integrations/websockets/clients.md) and
[socket contracts](integrations/websockets/sockets.md).

## MCP features

Install `fastapi-canon[mcp]` for FastMCP feature mounts with Dishka `Inject[T]`.
See the [MCP guide](integrations/mcp/README.md) for composition, namespaces,
endpoint defaults and supported execution lifetimes.

## Generate and use the SDK

Save `fastapi-canon.toml` at the project root. This configuration exports every
contract the app above has (HTTP/SSE, RPC and WebRTC) into `contracts/` and
generates both language targets from them:

```toml
[codegen]
version = 1
app = "src.app:app"

[codegen.targets.browser]
language = "typescript"
output = "frontend/generated"

[codegen.targets.python]
language = "python"
output = "api_client"
package = "api_client"
```

```console
uv run fastapi-canon generate
uv run fastapi-canon generate --check --diff
```

`app` derives one source per contract kind the application has: `http`
(HTTP and SSE), `rpc` and `webrtc`. An HTTP-only app therefore exports only
`contracts/openapi.json`; a stream-only addition uses the same OpenAPI source as
HTTP.
`--check --diff` checks generated files and exported contracts without writing;
use it in CI to catch drift. Offline generation can consume saved contracts
without importing application code.

The generated TypeScript API:

```typescript
import { createClient, ProblemError } from "./generated";

const client = createClient({ baseUrl: "http://localhost:8000" });
try {
  const project = await client.projects.get({ projectId: 1 });
  for await (const event of client.projects.events({ projectId: project.id })) {
    console.log(event.sequence);
  }
  await client.projects.cancel({ projectId: project.id });
} catch (error) {
  if (error instanceof ProblemError) console.error(error.problem.type);
  else throw error;
} finally {
  await client.close();
}
```

The same domain API in Python, inside an async function:

```python
from api_client import ApiClient, ProblemError

async with ApiClient("http://localhost:8000") as client:
    try:
        project = await client.projects.get(project_id=1)
        async for event in client.projects.events(project_id=project.id):
            print(event.sequence)
        await client.projects.cancel(project_id=project.id)
    except ProblemError as error:
        print(error.problem.type)
```

Generated SDKs do not depend on Canon. Python consumers install Pydantic, `httpx`
(or configured `httpx2`) and `websockets` for RPC; TypeScript uses fetch/DOM APIs
and the `ESNext.Disposable` type library. Bindings connect lazily, and closing the
client releases resources it owns. Shared semantic model identities avoid
duplicating DTOs across contracts.

The [codegen guide](unified-canon-concept.md) covers naming, model identity,
connection/auth options, operation selection and output ownership. It also
documents limits: unified HTTP generation supports JSON and declared SSE;
unsupported multipart/file operations must be explicitly excluded. WebRTC
generation targets TypeScript. The [runnable canonical-errors example](../examples/canonical_errors/app.py)
includes a complete multi-transport configuration.



## Check the contract in CI

```console
uv run fastapi-canon check errors src --strict --require-complete
uv run fastapi-canon generate --check --diff
```

The error checker reports missing mappings, missing declarations and paths it
cannot resolve. It analyzes supported source paths; keep behavioral tests for
runtime behavior. See [error checking](error-checking.md) for analysis limits.

Next: [application structure](composition.md), [stream behavior](streams.md),
[WebSockets](integrations/websockets/README.md) and [instrumentation](telemetry.md).
