# fastapi-canon

Canon adds typed HTTP contracts, SSE, WebSockets and application composition to
FastAPI. Define public errors once, reuse them across transports and export
contracts for Python and TypeScript clients.

## Install

```console
uv add fastapi-canon
```

Python 3.12–3.14 is supported. Add extras for the features you use, for example
`fastapi-canon[sse,websockets]` or `fastapi-canon[codegen]`.

**Published: 0.19.0 (2026-11-10).**
These docs follow the checkout. Follow the
[migration guide](docs/package-migration.md#upgrade-to-0190) when upgrading.
See the [release and verified artifacts](https://github.com/mathisarends/fastapi-canon/releases/tag/fastapi-canon-v0.19.0).
This checkout prepares unpublished 0.21.0; see its
[migration steps](docs/package-migration.md#upgrade-to-0210).

## Shared error handling

Keep domain exceptions in ordinary Python and declare their public representation:

```python
from fastapi import FastAPI

from fastapi_canon import APIRouter, Composition, Error, Feature


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


@router.get("/projects/{project_id}", raises=[project_missing])
async def get_project(project_id: int) -> dict[str, int]:
    if project_id == 0:
        raise ProjectMissing()
    return {"id": project_id}


app = Composition(
    features=(Feature(name="projects", routers=[router]),),
).build(FastAPI())
```

`GET /projects/0` returns a 404 RFC 9457 Problem Details response.
OpenAPI describes the same error; exception messages stay private by default.
Reuse `project_missing` in SSE and RPC declarations. See the
[quickstart](docs/quickstart.md) for the complete server and clients.

## What you can build

| Task | Guide |
| --- | --- |
| HTTP routes, dependencies and feature lifecycles | [Composition](docs/composition.md) |
| Shared public errors and typed extensions | [Problems](docs/problems.md) |
| File uploads and binary downloads | [File transfers](docs/integrations/uploads.md) |
| Typed event streams | [SSE](docs/integrations/sse/README.md) |
| RPC, sockets and binary streams | [WebSockets](docs/integrations/websockets/README.md) |
| Media sessions, tools or database resources | [WebRTC](docs/integrations/webrtc/README.md), [MCP](docs/integrations/mcp/README.md), [database](docs/database.md) |
| Service tracing and request logs | [Telemetry](docs/telemetry.md), [logging](docs/request-logging.md) |

## Generate and check clients

The generation workflow exports contracts and runs specialized SSE,
WebSocket and WebRTC generators. Add your HTTP generator through external
commands. Configure it with the [generation guide](docs/codegen.md), then run:

```console
uv run fastapi-canon generate
uv run fastapi-canon generate --check --diff
uv run fastapi-canon check errors src --strict --require-complete
```

Python models and error declarations are shared on the server. Generated
clients have separate APIs and error classes. Static checks complement runtime
tests and report paths they cannot resolve.

Browse the [task guides](docs/README.md), [examples](examples/README.md) or
[contributor commands](docs/contributing.md). Coding agents can install the
[bundled backend skill](fastapi_canon/skills/build-canon-backend/SKILL.md)
with `uv run fastapi-canon skill install`.
