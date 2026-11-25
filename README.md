# fastapi-canon

[![PyPI](https://img.shields.io/pypi/v/fastapi-canon?color=blue)](https://pypi.org/project/fastapi-canon/)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-canon)](https://pypi.org/project/fastapi-canon/)
[![CI](https://github.com/mathisarends/fastapi-canon/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mathisarends/fastapi-canon/actions/workflows/ci.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2a6db2)](https://mypy-lang.org/)
[![License](https://img.shields.io/pypi/l/fastapi-canon)](LICENSE)

**FastAPI for streams, WebSockets and AI apps, with typed clients.**

FastAPI is great at JSON endpoints. Token streams, WebSockets and their errors
are left to you, and none of it shows up in OpenAPI. Canon makes them typed
contracts and generates Python and TypeScript clients.

**Plain FastAPI:** untyped events, a homemade error event.

```python
@app.post("/chats/{chat_id}/reply", response_class=EventSourceResponse)
async def reply(chat_id: str) -> AsyncIterator[ServerSentEvent]:
    try:
        async for text in model.stream(chat_id):
            yield ServerSentEvent(data={"text": text})
    except QuotaExceeded:
        yield ServerSentEvent(event="error", data={"message": "quota"})
```

**Canon:** typed events, a declared error.

```python
class Token(BaseModel):
    text: str


quota_exceeded = Error(QuotaExceeded, status=429, code="quota_exceeded")


@router.post(
    "/chats/{chat_id}/reply",
    response_class=EventSourceResponse,
    raises=[quota_exceeded],
)
async def reply(chat_id: str) -> AsyncIterator[Token]:
    async for text in model.stream(chat_id):
        yield Token(text=text)
```

## What Canon adds

- **Errors, declared once.** Raise plain exceptions; get the response, OpenAPI
  entry and client error from one `Error`.
- **Typed SSE.** Clients iterate typed events and know when a stream failed.
- **WebSocket RPC.** Typed methods and streams, no request-ID plumbing.
- **Everything in the contract.** Streams, sockets and WebRTC, checked in CI.
- **One app model.** Features, Dishka dependencies and lifecycles for every transport.

## Installation

```console
uv add fastapi-canon
```

Python 3.12–3.14. Extras: `sse`, `websockets`, `webrtc`, `mcp`, `codegen`.
Upgrading? See the [changelog](CHANGELOG.md) and [migration guide](docs/package-migration.md).

## Examples

Five steps, each building on the last.

### 1. Map a domain error

Raise a plain exception; `Error` decides what the client sees.

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

`GET /projects/0` → 404 Problem Details, documented in OpenAPI.

### 2. Stream agent events

Yield models; clients get typed events.

```python
class TokenDelta(BaseModel):
    type: Literal["token_delta"] = "token_delta"
    text: str


class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    name: str
    arguments: dict[str, str]


RunEvent = Annotated[TokenDelta | ToolCall, Field(discriminator="type")]


@router.get(
    "/runs/{run_id}/events",
    response_class=EventSourceResponse,
    raises=[run_missing],
)
async def run_events(run_id: str) -> AsyncIterator[RunEvent]:
    yield ToolCall(name="search", arguments={"query": "weather"})
    yield TokenDelta(text="Sunny")
```

### 3. Call methods over WebSockets

Methods and streams in one channel, on the same router.

```python
from fastapi_canon.integrations.websockets import RpcChannel

runs = RpcChannel(namespace="runs")


@runs.method(raises=[run_missing])
async def cancel(run_id: str) -> None:
    await scheduler.cancel(run_id)


@runs.stream()
async def answer(prompt: str) -> AsyncIterator[TokenDelta]:
    async for text in model.stream(prompt):
        yield TokenDelta(text=text)


router.include_channel(runs, path="/ws")
```

`run_missing` works the same on HTTP, SSE and RPC.

### 4. Inject services and compose features

Request services with `Inject[T]`; features bring their providers.

```python
from dishka import Provider, Scope, provide

from fastapi_canon import Inject


class RunProvider(Provider):
    store = provide(RunStore, scope=Scope.APP)


@router.get("/runs/{run_id}", raises=[run_missing])
async def get_run(run_id: str, store: Inject[RunStore]) -> Run:
    return await store.get(run_id)


runs_feature = Feature(name="runs", routers=[router], providers=[RunProvider()])
app = Composition(features=(runs_feature,)).build(FastAPI())
```

### 5. Generate and check clients

```console
uv run fastapi-canon generate
uv run fastapi-canon generate --check --diff
uv run fastapi-canon check errors src --strict --require-complete
```

The [quickstart](docs/01-quickstart.md) runs all five steps end to end.

### Runnable applications

| Level | Example | Shows |
| --- | --- | --- |
| 1 | [Projects API](examples/canonical_errors/README.md) | One error across HTTP, SSE, RPC and generated clients |
| 2 | [Typed streams](examples/streams/README.md) | Authenticated GET and POST streams of an event union |
| 3 | [WebSockets](examples/websockets/README.md) | Methods, notifications, protocols and raw sockets |
| 4 | [Feature showcase](examples/showcase/README.md) | Feature slices with domain errors and Dishka services |
| 5 | [Reference service](examples/reference-service/README.md) | Authentication, persistence and transactions |
| ★ | [WebRTC remote video](examples/webrtc/remote_video/README.md), [database](examples/database/README.md) | Media sessions; database resources and migrations |

## Documentation

The guides form a learning path; each level builds on the previous one.

| Level | Guide | You learn |
| --- | --- | --- |
| 1 | [From Python to typed clients](docs/01-quickstart.md) | HTTP, SSE and RPC with one error and generated clients |
| 2 | [Shared problems](docs/02-problems.md) | Public errors, extensions and headers |
| 3 | [Streaming](docs/03-streaming.md) | Event unions, admission checks and POST streams |
| 4 | [WebSockets](docs/04-websockets.md) | RPC methods, message streams and notifications |
| 5 | [Application composition](docs/05-composition.md) | Features, Dishka services, lifecycles and router policies |
| 6 | [Client generation](docs/06-codegen.md) | Generator configuration and drift checks in CI |
| 7 | [Static error checks](docs/07-error-checking.md) | Finding undeclared errors before runtime |
| 8 | [File transfers](docs/08-file-transfers.md) | Uploads, multipart forms and binary downloads |
| 9 | [Database resources](docs/09-database.md) | Sessions, transactions and migrations |
| 10 | [Instrumentation](docs/10-telemetry.md) | Tracing services and correlating [request logs](docs/request-logging.md) |
| ★ | [WebRTC](docs/integrations/webrtc/README.md), [MCP](docs/integrations/mcp/README.md) | Media sessions; tools for agents |

Browse the [task guides](docs/README.md), [examples](examples/README.md) or
[contributor commands](docs/contributing.md). Coding agents can install the
[bundled backend skill](fastapi_canon/skills/build-canon-backend/SKILL.md)
with `uv run fastapi-canon skill install`.
