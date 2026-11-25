# fastapi-canon

[![PyPI](https://img.shields.io/pypi/v/fastapi-canon?color=blue)](https://pypi.org/project/fastapi-canon/)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-canon)](https://pypi.org/project/fastapi-canon/)
[![CI](https://github.com/mathisarends/fastapi-canon/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mathisarends/fastapi-canon/actions/workflows/ci.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2a6db2)](https://mypy-lang.org/)
[![License](https://img.shields.io/pypi/l/fastapi-canon)](LICENSE)

**Typed contracts for FastAPI beyond request/response JSON.**

FastAPI handles JSON endpoints well. Streams, WebSockets, WebRTC sessions and
their errors are left to you: you write the framing, invent an error format and
keep clients in sync by hand, because none of it appears in the OpenAPI schema.
AI applications hit this early, since token streams, tool calls and cancellable
runs are their normal case. Canon describes these operations as typed contracts
and generates Python and TypeScript clients from them.

**Plain FastAPI:** manual framing, an ad-hoc error event, no event schema.

```python
@app.post("/chats/{chat_id}/reply")
async def reply(chat_id: str) -> StreamingResponse:
    async def frames():
        try:
            async for text in model.stream(chat_id):
                yield f"data: {json.dumps({'text': text})}\n\n"
        except QuotaExceeded:
            yield 'event: error\ndata: {"message": "quota"}\n\n'

    return StreamingResponse(frames(), media_type="text/event-stream")
```

**Canon:** a typed stream with a declared failure.

```python
class Token(BaseModel):
    text: str


quota_exceeded = Error(QuotaExceeded, status=429, code="quota_exceeded")


@router.sse(
    "/chats/{chat_id}/reply",
    methods=[HTTPMethod.POST],
    raises=[quota_exceeded],
)
async def reply(chat_id: str) -> AsyncIterator[Token]:
    async for text in model.stream(chat_id):
        yield Token(text=text)
```

Generated clients yield `Token` objects, raise a typed problem for
`quota_exceeded` and tell a completed stream apart from a dropped connection.

## What Canon adds

- **Errors as declarations.** Handlers raise ordinary domain exceptions instead
  of `HTTPException`. One `Error` produces the RFC 9457 response, its OpenAPI
  entry and the client error, so documented and actual errors cannot diverge.
- **Typed SSE.** Event models, completion and terminal problems are part of the
  contract and become typed iterators in the clients.
- **WebSocket channels.** RPC methods, message streams, notifications and binary
  streams with typed clients that handle request IDs, correlation and cleanup.
- **Contracts for everything.** SSE events, RPC messages and WebRTC sessions are
  exported next to OpenAPI and checked for drift in CI.
- **One application model.** HTTP, streams, channels, WebRTC and MCP tools live in
  features with Dishka dependencies and managed lifecycles.

## Installation

```console
uv add fastapi-canon
```

Python 3.12–3.14 is supported. Add extras for the features you use, for example
`fastapi-canon[sse,websockets]` or `fastapi-canon[codegen]`. Read the
[changelog](CHANGELOG.md) and [migration guide](docs/package-migration.md)
before upgrading.

## Examples

Each step builds on the previous one.

### 1. Map a domain error

A complete application. The exception stays ordinary Python; `Error` declares
its public representation:

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

`GET /projects/0` returns a 404 Problem Details response, and OpenAPI documents
it. Exception messages stay private by default.

### 2. Stream agent events

A discriminated union becomes an exhaustively typed event in the clients:

```python
class TokenDelta(BaseModel):
    type: Literal["token_delta"] = "token_delta"
    text: str


class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    name: str
    arguments: dict[str, str]


RunEvent = Annotated[TokenDelta | ToolCall, Field(discriminator="type")]


@router.sse("/runs/{run_id}/events", raises=[run_missing])
async def run_events(run_id: str) -> AsyncIterator[RunEvent]:
    yield ToolCall(name="search", arguments={"query": "weather"})
    yield TokenDelta(text="Sunny")
```

A declared error raised before the first event returns an HTTP problem; raised
later, it ends the stream with a typed problem frame.

### 3. Call methods over WebSockets

Group RPC methods and message streams in a channel and mount it on the same
router:

```python
from fastapi_canon.integrations.websockets import RpcChannel

runs = RpcChannel("runs")


@runs.method(raises=[run_missing])
async def cancel(params: RunRef) -> None:
    await scheduler.cancel(params.run_id)


@runs.stream()
async def answer(params: Prompt) -> AsyncIterator[TokenDelta]:
    async for text in model.stream(params.text):
        yield TokenDelta(text=text)


router.include_channel(runs, path="/ws")
```

Clients call `runs.cancel` as a typed method and iterate `runs.answer`. The same
`run_missing` error arrives as a problem on HTTP, SSE and RPC.

### 4. Inject services and compose features

Features bundle routers with Dishka providers; handlers request services with
`Inject[T]`:

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

`build` checks every contract and provider before the application starts.

### 5. Generate and check clients

```console
uv run fastapi-canon generate
uv run fastapi-canon generate --check --diff
uv run fastapi-canon check errors src --strict --require-complete
```

The [quickstart](docs/quickstart.md) walks through all five steps with generated
Python and TypeScript clients.

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

| Task | Guide |
| --- | --- |
| HTTP routes, dependencies and feature lifecycles | [Composition](docs/composition.md) |
| Shared public errors and typed extensions | [Problems](docs/problems.md) |
| File uploads and binary downloads | [File transfers](docs/integrations/uploads.md) |
| Typed event streams | [SSE](docs/integrations/sse/README.md) |
| RPC, sockets and binary streams | [WebSockets](docs/integrations/websockets/README.md) |
| Media sessions, tools or database resources | [WebRTC](docs/integrations/webrtc/README.md), [MCP](docs/integrations/mcp/README.md), [database](docs/database.md) |
| Client generation and drift checks | [Generation](docs/codegen.md), [error checking](docs/error-checking.md) |
| Service tracing and request logs | [Telemetry](docs/telemetry.md), [logging](docs/request-logging.md) |

Browse the [task guides](docs/README.md), [examples](examples/README.md) or
[contributor commands](docs/contributing.md). Coding agents can install the
[bundled backend skill](fastapi_canon/skills/build-canon-backend/SKILL.md)
with `uv run fastapi-canon skill install`.
