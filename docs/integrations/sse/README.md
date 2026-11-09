# Typed HTTP event streams

Use SSE when a client needs a sequence of typed events over HTTP: generated
tokens, progress updates or notifications. Install `fastapi-canon[sse]`; add
the `codegen` extra where you generate clients.

The [quickstart](../../quickstart.md) shows a Python stream and both generated
clients in one application. For an authenticated runnable stream, use the
[stream recipe](../../../examples/streams/README.md).

## Define the contract

Declare an `AsyncIterator[EventModel]` with `@router.sse(...)`. Use `raises=`
for public failures and dependencies for admission checks. A generator body
runs after the response has started, so checks inside it cannot reject the
initial HTTP request.

Before response start, mapped failures return HTTP Problem Details. During
iteration, a declared failure produces `canon.problem` and ends the stream.
Normal completion produces `canon.complete`. Generated clients consume those
control frames: business events are iterator values, terminal problems raise
`ProblemError`, and an unexpected EOF raises `TransportError`.

Close the client when it is no longer needed. When leaving an iterator early,
use its supported cleanup API; see [client cancellation](clients.md#cancellation-and-errors).
Canon does not add application replay or reconnection semantics.

## Go further

- [Server contracts](../../streams.md): authentication, POST streams, scopes and errors.
- [Client usage](clients.md): typed values, cancellation and validation.
- [Generation options](codegen.md): export, validation and CI checks.
- [Existing SSE endpoints](native-transports.md): contract-first generation and proxies.
- [Generation workflow](../../codegen.md): coordinate separate clients.
