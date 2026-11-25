# 4. WebSockets

Group typed methods, streams and events in a channel and mount it next to your
HTTP routes. Install `fastapi-canon[websockets]`.

## Methods

```python
from fastapi_canon import APIRouter, Inject
from fastapi_canon.integrations.websockets import RpcChannel

tasks = RpcChannel(namespace="tasks")


@tasks.method(raises=[task_missing])
async def complete(task_id: int, service: Inject[TaskService]) -> Task:
    return await service.complete(task_id)


router = APIRouter()
router.include_channel(tasks, path="/ws")
```

Parameters work like FastAPI: named parameters form the request, a single
Pydantic model is the whole request, `Inject[T]` resolves services. The client
calls `tasks.complete`:

```python
task = await client.tasks.complete(task_id=1)
```

Request IDs, correlation, timeouts and typed errors are handled for you.

## Streams

Return an `AsyncIterator` to send many results for one call, such as LLM tokens
or job progress:

```python
@tasks.stream()
async def progress(task_id: int) -> AsyncIterator[Progress]:
    yield Progress(percent=0)
    yield Progress(percent=100)
```

```python
async with client.tasks.progress(task_id=1) as updates:
    async for update in updates:
        print(update.percent)
```

## Events

Push notifications to every connected client:

```python
@tasks.event()
async def updated(events: Inject[TaskEvents]) -> AsyncIterator[TaskUpdated]:
    async for update in events.subscribe():
        yield update
```

## Which one?

| Need | Use | Guide |
| --- | --- | --- |
| One result | `@channel.method()` | [Methods](integrations/websockets/01-services.md) |
| Many results for one call | `@channel.stream()` | [Message streams](integrations/websockets/02-message-streams.md) |
| Server-initiated updates | `@channel.event()` | [Connections and events](integrations/websockets/03-connections-and-events.md) |
| Raw bytes | `@router.binary_stream()` | [Binary streams](integrations/websockets/04-streams.md) |
| An existing protocol | typed or raw socket | [Sockets](integrations/websockets/05-sockets.md) |

## Go deeper

[Authentication](integrations/websockets/06-dependencies.md) ·
[errors](integrations/websockets/07-errors.md) ·
[clients](integrations/websockets/08-clients.md) ·
[telemetry](integrations/websockets/09-observability.md) ·
[hardening](integrations/websockets/10-hardening-overview.md) ·
[migration](integrations/websockets/11-migration.md) ·
[wire protocol](integrations/websockets/12-protocol.md) ·
[transport adapters](integrations/websockets/13-transports.md)

---

Previous: [3. Streaming](03-streaming.md) · [Learning path](README.md) · Next: [5. Composition](05-composition.md)
