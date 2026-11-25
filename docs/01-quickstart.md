# 1. Quickstart

Build a small Projects API with one HTTP route, one SSE stream and one WebSocket
method that share an error. Then generate typed clients for it.

```console
uv add "fastapi-canon[sse,websockets]"
uv add --dev "fastapi-canon[codegen]" uvicorn
```

Put everything below in `src/app.py` (and an empty `src/__init__.py`). The
finished version is the [Projects example](../examples/canonical_errors/README.md).

## Step 1: a route with a public error

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
```

`GET /projects/0` returns a 404 `application/problem+json` response, and
OpenAPI documents it. The exception stays plain Python.

## Step 2: a typed stream

```python
from collections.abc import AsyncIterator

from fastapi import Depends
from fastapi.sse import EventSourceResponse


class ProjectEvent(BaseModel):
    sequence: int


@router.get(
    "/projects/{project_id}/events",
    response_class=EventSourceResponse,
    operation_id="projects.events",
    dependencies=[Depends(require_project)],
    raises=[project_missing],
)
async def events(project_id: int) -> AsyncIterator[ProjectEvent]:
    yield ProjectEvent(sequence=1)
    if project_id == 2:
        raise ProjectMissing()
```

The dependency runs before the stream starts, so project `0` still gets an HTTP
404. Project `2` fails mid-stream: clients receive a typed problem instead of a
silently closed connection.

## Step 3: a WebSocket method

```python
from fastapi_canon.integrations.websockets import RpcChannel

projects = RpcChannel(namespace="projects")


@projects.method(raises=[project_missing])
async def cancel(project_id: int) -> None:
    require_project(project_id)


router.include_channel(projects, path="/ws")
```

The same `project_missing` error now covers HTTP, SSE and RPC.

## Step 4: compose and run

```python
app = Composition(
    features=(Feature(name="projects", routers=[router]),),
    errors=ErrorOptions(type_base="https://api.example.com/problems"),
).build(FastAPI())
```

```console
uv run uvicorn src.app:app --reload
```

`build` validates every contract before the app starts.

## Step 5: generate clients

Save `fastapi-canon.toml` next to `src/`:

```toml
[codegen]
version = 2
app = "src.app:app"

[codegen.targets.streams]
generator = "sse"
output = "generated/streams"

[codegen.targets.rpc]
generator = "websockets"
output = "generated/rpc"
```

```console
uv run fastapi-canon generate
```

You get a TypeScript stream client and an RPC client. Plug in your preferred
OpenAPI generator for plain HTTP; [client generation](06-codegen.md) shows how.

## Step 6: keep it honest in CI

```console
uv run fastapi-canon generate --check --diff
uv run fastapi-canon check errors src --strict --require-complete
```

The first command fails when clients drift from the server. The second finds
exceptions that a route can raise but does not declare.

---

[Learning path](README.md) · Next: [2. Errors](02-errors.md)
