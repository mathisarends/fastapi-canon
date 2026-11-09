# Message streams

`@channel.stream()` declares an operation which delivers a sequence of results.
`AsyncIterator[T]` selects JSON messages; `AsyncIterator[bytes]` selects binary
frames on a dedicated endpoint. The signature is inspected at registration;
no generator is executed to discover its transport. Use explicit annotations.
Unconstrained `Any` and a union mixing raw bytes with messages are rejected.
Scalar values, models, collections and supported model unions are valid messages.

```python
from collections.abc import AsyncIterator

from fastapi_canon.integrations.websockets import (
    CanonWebSocketRouter,
    RpcChannel,
    RpcModel,
)


class JobParams(RpcModel):
    job_id: str


class Progress(RpcModel):
    percent: int


jobs = RpcChannel("jobs")


@jobs.stream()
async def progress(params: JobParams) -> AsyncIterator[Progress]:
    yield Progress(percent=0)
    yield Progress(percent=100)


@jobs.stream(content_type="application/octet-stream")
async def download() -> AsyncIterator[bytes]:
    yield b"first chunk"
    yield b"second chunk"


rpc = CanonWebSocketRouter()
rpc.socket("/rpc", channels=[jobs])
rpc.stream("/download", download)
```

Include `rpc` in a FastAPI application or a Canon feature. Message operations
keep `Inject[T]`, parameter validation, channel error declarations, `raises=`,
request middleware and tracing. Content types apply only to binary streams.
The [binary guide](streams.md) also covers input-only and duplex handlers using
`RpcBinaryInput` / `RpcBinaryOutput`, half-close and backpressure.

## Generated Python

Both receive APIs use the same resource pattern:

```python
async with client.jobs.progress(job_id="report") as updates:
    async for update in updates:
        print(update.percent)

async with client.jobs.download() as chunks:
    async for chunk in chunks:
        print(chunk)
```

Entering a message stream opens it without waiting for its first data item.
Leaving the block cancels unfinished work and awaits cleanup. The iterator also
supports direct iteration and `aclose()`; use a context when leaving early.
`aclose()` interrupts a pending read. Each stream resource has one consumer and
cannot be entered twice. These lifetimes belong to the specialized RPC client.

## Generated TypeScript

```typescript
await using updates = await client.jobs.progress({ jobId: "report" });
for await (const update of updates) {
  console.log(update.percent);
}

await using chunks = await client.jobs.download();
for await (const chunk of chunks) {
  console.log(chunk);
}
```

Opening returns a promise. The resolved message stream supports async iteration,
`close()` and `Symbol.asyncDispose`; loop exit also cancels it. Binary receive
streams retain their existing disposal API. Upload completion remains distinct
from aborting a transfer: use `end()` / `endInput()` as described in the binary
guide.

## Lifecycle and wire contract

The JSON-RPC binding uses `<operation>.open`, returning `{streamId: string}`.
Messages on `<operation>` contain `{streamId, payload}`. The server finishes
with `{streamId, complete: true}` or `{streamId, complete: false, error}`.
`<operation>.cancel({streamId})` stops that generator. Repeated cancellation is
idempotent; it does not cancel another stream or close the shared connection.
The generator's `finally` block runs on cancellation and disconnect.

Closing a stream ends that result delivery. Cancelling an independently owned
domain job remains a separate application operation. The server's request
deadline bounds the opening/cancellation handshake, not the generator lifetime.
`RpcLimits.max_streams` limits active message streams per connection (100 by
default). OpenRPC records JSON framing, direction and delivery policy in
`x-rpc-streams`; binary bindings retain `x-rpckit-binary-streams` and their
explicit endpoint metadata. Generated clients choose the binding from that
contract.

## Resilience and slow consumers

Connections retain lazy opening, retry limits, credential refresh, heartbeat,
state callbacks, graceful drain and existing error mapping. With opt-in
`reconnect=True` / `reconnect: true`, a disconnected message stream reopens with
fresh parameters and a fresh stream ID. Cancellation for an old ID stays on its
issuing transport. Closing while recovery is pending prevents reopening.

Reopening restarts the source. It does not guarantee replay or exact continuation;
sources requiring continuity must expose an application cursor or resume token.
Without reconnect, connection failure terminates the stream. Exhausted recovery
and permanent rejection surface the terminal cause.

A full message-stream consumer queue fails that stream explicitly. A shared
transport queue overflow that would discard stream data fails the transport.
Passive events retain their configurable overflow behavior and counters. This
bounded-buffer policy avoids silently turning a partial result into successful
completion; it does not promise durable delivery across disconnects. Binary
streams retain socket backpressure.

For upgrades, follow the [0.15.0 migration](../../package-migration.md#upgrade-to-0150).
The old `subscription()` decorator, `.subscribe` / `.unsubscribe` controls and
`subscriptionId` are removed. Regenerate clients and upgrade with the server.
