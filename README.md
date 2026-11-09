# fastapi-canon

Canon adds shared error handling, typed streams, WebSocket APIs and client
generation to FastAPI. Public error definitions are shared across HTTP, SSE and
WebSocket RPC, exported contracts, and generated Python and TypeScript clients.

## Contents

- [What Canon handles](#what-canon-handles)
- [Install](#install)
- [Shared error handling](#shared-error-handling)
- [HTTP, SSE and WebSocket clients](#http-sse-and-websocket-clients)
- [Instrument application boundaries](#instrument-application-boundaries)
- [Check contracts in CI](#check-contracts-in-ci)
- [Learn more](#learn-more)

## What Canon handles

- **HTTP, SSE and WebSockets:** typed contracts, event streams and RPC methods,
  with specialized transport clients and external HTTP generation.
- **File transfers:** single and multiple multipart uploads with form fields,
  plus binary download contracts for OpenAPI tooling. See the [upload guide](docs/integrations/uploads.md)
  for the unreleased source API.
- **Public errors:** map domain exceptions once, declare them with `raises=`,
  and carry the contract into HTTP RFC 9457 Problem Details, streams and SDKs.
- **Static checks and code generation:** find missing error mappings and
  declarations, report incomplete analysis, and catch generated-client drift in CI.
- **Application composition:** features contribute routers, Dishka providers and
  lifespans; composition owns installation and cleanup.
- **Instrumentation:** declare observed services and ports at the feature boundary
  without adding telemetry annotations to business logic.

Generated SDKs do not depend on Canon. Python clients use Pydantic, an HTTP client
and, for RPC, `websockets`; TypeScript clients use fetch/DOM APIs.
See [client generation](docs/codegen.md) for supported operations
and target requirements.

## Install

```console
uv add "fastapi-canon[sse,websockets]"
uv add --dev "fastapi-canon[codegen]"
```

For HTTP-only applications, use `uv add fastapi-canon`. The base includes
FastAPI, Pydantic and Dishka. Python 3.12–3.14 is supported.

The current published release is **0.16.0**. See the
[upgrade guide](docs/package-migration.md#upgrade-to-0160) and
[tested compatibility](docs/compatibility.md). Upgrade servers and regenerated
clients together.

## Shared error handling

Keep domain exceptions in ordinary Python. Define their public representation
once with `Error`, then declare it on operations with `raises=`:

```python
from fastapi import FastAPI

from fastapi_canon import APIRouter, Composition, Error, ErrorOptions, Feature


class ProjectMissing(Exception):
    pass


project_missing = Error(
    ProjectMissing,
    status=404,
    code="project_missing",
    title="Project not found",
    detail="Choose another project.",
)
router = APIRouter()


@router.get(
    "/projects/{project_id}",
    operation_id="projects.get",
    raises=[project_missing],
)
async def get_project(project_id: int) -> dict[str, int]:
    if project_id == 0:
        raise ProjectMissing()
    return {"id": project_id}


app = Composition(
    features=(Feature(name="projects", routers=[router]),),
    errors=ErrorOptions(type_base="https://api.example.com/problems"),
).build(FastAPI())
```

`GET /projects/0` returns a 404 `application/problem+json` response following
RFC 9457, with type `https://api.example.com/problems/project_missing` and code
`project_missing`. OpenAPI documents the same problem. Exception messages stay
private by default; the declared detail is public.

Reuse the same `project_missing` object in `@router.sse(..., raises=[...])` and
`@channel.method(raises=[...])`. There is one domain-to-public-error mapping:

| Where the failure occurs | How it reaches the client |
| --- | --- |
| HTTP request or SSE admission | HTTP Problem Details response |
| During an SSE stream | Terminal `canon.problem` event |
| WebSocket RPC method | Problem data in the JSON-RPC error envelope |

These bindings preserve the problem's `type`, `code` and declared public fields.
HTTP status is included in HTTP problems and omitted from SSE terminal and RPC
problems. RFC 9457 defines HTTP Problem Details; the stream and RPC bindings are
Canon conventions.

Generated SSE/RPC clients expose public problem data through `ProblemError`:

```python
from rpc_client import ProblemError

try:
    await client.projects.cancel(project_id=0)
except ProblemError as error:
    print(error.problem.type)
```

This client excerpt runs inside an async function with a generated client.
The [quickstart](docs/quickstart.md) shows the complete server and SDK setup;
[shared problems](docs/problems.md) covers typed extensions, validation failures
and safe internal errors.

## HTTP, SSE and WebSocket clients

Use an OpenAPI generator such as Orval with your framework's query integration
for HTTP. Canon generates specialized SSE, WebSocket and WebRTC clients.
Each client retains its transport's cancellation and resource ownership rules.

Configure a [generation workflow](docs/codegen.md), then run:

```console
uv run fastapi-canon generate
uv run fastapi-canon generate --check --diff
```

The workflow exports contracts once, runs selected generators and reports drift.
HTTP tooling runs through explicit external commands. There is no Canon HTTP
client generator, shared client facade or cross-protocol model merger.

Reuse Python models and `Error` definitions on the server. Each generator derives
types from exported contracts. Separate generated error classes retain separate
runtime identities. See the [shared-error example](examples/canonical_errors/README.md),
[SSE clients](docs/integrations/sse/clients.md) and
[RPC clients](docs/integrations/websockets/clients.md).


## Instrument application boundaries

Add the `otel` extra and declare the services or ports to observe:

```python
from fastapi_canon import Feature
from fastapi_canon.integrations.opentelemetry import instrument

tasks = Feature(
    name="tasks",
    routers=(router,),
    providers=(TaskProvider,),
    instrumentation=instrument(
        TaskRepository,
        TaskService,
        expected=(TaskNotFound,),
    ),
)
```

This excerpt uses your application's router, provider and service types.
Enable it with `Composition(..., telemetry=Telemetry(...))`. Canon observes
declared async methods through Dishka and adds port spans alongside HTTP
telemetry. Your application owns SDK providers and exporters.
See the [instrumentation guide](docs/telemetry.md) for a complete setup.

## Check contracts in CI

```console
uv run fastapi-canon check errors src --strict --require-complete
uv run fastapi-canon generate --check --diff
```

The static checker follows supported application calls and dependencies; it
reports unresolved paths rather than claiming complete coverage. Use it alongside
runtime tests. The generator checks exported contracts and client files for drift.

## Learn more

Start with the [quickstart](docs/quickstart.md), then choose a
[task guide](docs/README.md) or a [runnable example](examples/README.md).

Optional integrations also include [WebRTC media sessions](docs/integrations/webrtc/README.md),
[MCP](docs/integrations/mcp/README.md) and [database resources](docs/database.md).

For coding agents, install the bundled
[`build-canon-backend` skill](fastapi_canon/skills/build-canon-backend/SKILL.md)
from your application project:

```console
uv run fastapi-canon skill install
```

For updates and destination options, see
[the skill installation guide](docs/package-migration.md#agent-skill).
Contributor commands and repository architecture live in
[contributing](docs/contributing.md).
