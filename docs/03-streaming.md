# 3. Streaming

Send a sequence of typed events over HTTP: tokens, progress, notifications.
Install `fastapi-canon[sse]`.

## A typed stream

```python
from collections.abc import AsyncIterator

from fastapi import Depends
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel

from fastapi_canon import APIRouter, Inject

router = APIRouter(prefix="/runs")


class RunEvent(BaseModel):
    run_id: str
    message: str


async def require_run(run_id: str, runs: Inject[RunService]) -> None:
    await runs.require_existing(run_id)


@router.get(
    "/{run_id}/events",
    response_class=EventSourceResponse,
    operation_id="runs.events",
    raises=[run_not_found],
    dependencies=[Depends(require_run)],
)
async def events(run_id: str) -> AsyncIterator[RunEvent]:
    yield RunEvent(run_id=run_id, message="Started")
    yield RunEvent(run_id=run_id, message="Completed")
```

A plain FastAPI SSE route; the return annotation is the contract. Every stream
ends with either `canon.complete` or a `canon.problem` event, so clients can
tell "done" from "connection dropped".

## The one rule: check before you stream

The generator body runs **after** the response has started. Anything that should
fail with an HTTP status (authentication, missing resources) belongs in a
dependency, like `require_run` above.

| Fails in | Client sees |
| --- | --- |
| a dependency | HTTP problem response (401, 404, …) |
| the generator | `canon.problem` event, then the stream ends |
| the network | a transport error |

## POST streams

Use `@router.post` when the request needs a JSON body, as most chat and agent
endpoints do:

```python
@router.post("/chat", response_class=EventSourceResponse, raises=[quota_exceeded])
async def chat(prompt: Prompt) -> AsyncIterator[Token]:
    async for text in model.stream(prompt.text):
        yield Token(text=text)
```

## Event unions

A discriminated union gives clients exhaustive, typed events:

```python
RunEvent = Annotated[TokenDelta | ToolCall | RunCompleted, Field(discriminator="type")]
```

## Services in long streams

Request-scoped services close before the stream starts. For database work
during a stream, inject a `ServiceScope` and open short scopes as needed; see
[short scopes](09-database.md#short-scopes-for-workers-and-streams).

## Use it from a client

The operation ID `runs.events` becomes `client.runs.events`:

```python
from generated.streams import Client

async with Client("http://localhost:8000") as client:
    async for event in client.runs.events(run_id="demo"):
        print(event.message)
```

## Go deeper

- [Clients](integrations/sse/01-clients.md): TypeScript usage, cancellation, validation
- [Generation options](integrations/sse/02-codegen.md): naming, diagnostics, CI policy
- [Existing SSE endpoints](integrations/sse/03-native-transports.md): generate clients for SSE endpoints outside Canon
- Runnable: [authenticated GET and POST streams](../examples/streams/README.md)

---

Previous: [2. Errors](02-errors.md) · [Learning path](README.md) · Next: [4. WebSockets](04-websockets.md)
