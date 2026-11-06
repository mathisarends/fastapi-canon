# DX-027: Evaluate context terminology across connection and server APIs

- Status: Done
- Priority: P3
- Kind: Decision
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: D6.

## Problem and reproduction

The WebSocket router accepts context= as a connection-context function, while
RpcChannel.create_server(context=...) accepts a value/object map.
Both patterns are documented in
[dependencies](../docs/integrations/websockets/dependencies.md).

## Scope

Determine whether clearer documentation suffices before proposing a public
parameter rename. Do not introduce another parallel context API by default.

## Acceptance criteria

- [x] Show both current patterns with complete runnable examples and identify a concrete confusing use case.
- [x] Distinguish context factories, hook results, injected services and standalone server values.
- [x] Compare a documentation fix with a parameter rename and account for every affected facade/example.
- [x] If a rename is recommended, follow the WebSocket compatibility rules and specify a next-minor migration.
- [x] Record a concrete decision; no rename is required merely because the same word appears twice.

## Verification

Verify the current signatures and run the existing context/hook scenarios; check guide links.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Decision

**Decision: documentation fix, no rename.** `context=` on `include_channel` and the
WebSocket router (an async function run per connection, with `hooks=`) and
`RpcChannel.create_server(context=...)` (an object or type-to-value mapping) keep
their names.

Current signatures, checked in the source:

- `include_channel(..., context: Callable[..., Awaitable[Any]] | None, hooks: Sequence[Callable[..., Any]] = ())`
  (`adapters/fastapi.py`); `hooks=` without `context=` is rejected, and a
  non-async `context=` raises `TypeError`.
- `RpcChannel.create_server(..., context: object | Mapping[type[Any], object] | None)`
  (`channel.py`).

The confusing case is a reader who sees `context=` on both and swaps the
arguments. Verified with a handler using `Inject[Store]`: passing a mapping to
`include_channel` fails immediately (`TypeError: context= expects an async
function`), but passing a connection-context function to `create_server` is
accepted and registered as a plain object of the function's type, so the failure
only appears when the handler resolves `Store`. Renaming `create_server`'s
parameter (for example to `values=`) would be a public signature change that
needs a next-minor release, an update of every guide, example and the bundled
skill, and it would still leave the router-level `context=`/`hooks=` pair
unchanged. A new parallel parameter is rejected. The gain does not justify that
cost, so the guide now has a glossary that separates connection context, hooks,
injected services and server values and tells the reader which to use.

## Completion

- Implementation or decision PR: PR_LINK
- Final commit and verification: documentation only. Signatures verified in `fastapi_canon/integrations/websockets/adapters/fastapi.py`
and `channel.py`; the swapped-argument behavior was reproduced with a throwaway
script. The existing context/hook scenarios are unchanged and were not
modified; no public signature, default or generated client changed.
- Remaining follow-ups: optional hardening, not created: reject a callable
  `context=` in `create_server` with a pointer to `include_channel(context=...)`.
  That is a stricter accepted input and therefore a breaking change under the
  release policy. Revisit a rename only if the glossary proves insufficient.
