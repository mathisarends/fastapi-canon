# DX-031: Split the WebSocket facade by user task

- Status: Open
- Priority: P2
- Kind: Feature (breaking public API)
- Baseline: `452a4b0` (2026-11-07)
- Depends on: Completed facade decision [#102](https://github.com/mathisarends/fastapi-canon/pull/102)
- Earlier review: S2

## Problem and reproduction

Current workflow inspection, not a bug:

```console
uv run --frozen python -c "import fastapi_canon.integrations.websockets as ws; print(len(ws.__all__))"
```

The facade still exports 75 names. The historical inventory in
[#102](https://github.com/mathisarends/fastapi-canon/pull/102) found 48 names
without documented consumer imports; refresh that inventory before implementation.
Channel and socket declarations share autocomplete with JSON-RPC envelopes,
endpoint and resolver internals, middleware authoring types, framework adapter
functions and protocol error classes.

## Scope

Apply the retained WebSocket decision below:
the facade keeps the endpoint-building names; middleware authoring moves to
`.middleware`, framework adapters to `.adapters`, protocol errors to `.errors`;
other names become internal unless a public signature uses them.

Entry points: [`websockets/__init__.py`](../fastapi_canon/integrations/websockets/__init__.py),
`websockets/adapters/__init__.py` (currently empty),
[`test_public_api.py`](../tests/unit/integrations/websockets/test_public_api.py),
`docs/integrations/websockets/`, `docs/canon.md`, `examples/websockets/`,
`docs/telemetry.md` and the integration [AGENTS.md](../fastapi_canon/integrations/websockets/AGENTS.md).

Out of scope: wire behavior, OpenRPC output and generated clients. Root facade
reduction and the SSE/WebRTC adapter changes already shipped in 0.16.0.

## Retained facade decision

This preserves the unfinished WebSocket part of the completed DX-023 decision
from [#102](https://github.com/mathisarends/fastapi-canon/pull/102).

A facade name is something an application author writes to declare, configure,
pass, catch, subclass or implement an API. Export count or unused consumer imports
alone do not justify removal. Every type used in a public signature retains a
public import path, which may be a documented submodule instead of the facade.
Only namespaces listed in [the public API guide](../docs/canon.md#public-namespaces)
are public; update that guide for the selected submodule paths.

The original proposed facade set is `RpcChannel`, `CanonWebSocketRouter`,
`RpcModel`, `Inject`, `RpcConnection`, `RpcHandshake`, `RpcReject`, `RpcRejection`,
`RpcErrorBinding`, `RpcError`, `RpcLimits`, `RpcDrain`, `RpcConnectionExpiry`,
`RpcConnectionClose`, `RpcDisconnect`, `RpcSocket`, `RpcSocketProtocol`,
`RpcMessageType`, `RpcBinaryInput`, `RpcBinaryOutput` and `ServerVariable`.
Refresh this set through the per-name signature check before removing exports.

The current `APIRouter` also provides `channel()`, `socket()` and
`binary_socket()`. The old rationale that raw sockets require a separate router
is obsolete. Evaluate actual uses of `CanonWebSocketRouter`, including custom
handlers, against these methods before deciding its final placement; this ticket
does not assume that it can be removed without a replacement.

Proposed placement for the other names:

- `.middleware`: `RpcMiddleware`, `RpcMiddlewareLike`, scope and outcome types,
  endpoint info types, `RpcRequestContext`, `RpcSuccess` and `RpcFailure`.
- `.adapters`: `FastApiSocket`, `serve_websocket`, `RpcServer` and
  `RpcResponseMessage`; load neither Dishka nor OpenTelemetry adapters eagerly.
- `.errors`: JSON-RPC protocol errors, `RpcErrorCode` and `ProtocolDefinitionError`.
- Signature types such as `RpcProtocol` retain a documented public submodule path
  unless the corresponding methods leave the public surface in the same change.
- Envelope types unused in public signatures, endpoint/contract internals and
  `RpcCodec` become internal. Document the logger name instead of exporting
  `LOGGER_NAME`; the Canon version remains available from `fastapi_canon`.

Do not add aliases for removed names. Update consumer imports, docs, examples and
the bundled skill together. Public API removals require the next minor release
with a complete old/new import table under `### Breaking changes`. At this review
0.16.0 is published, so the next breaking release is 0.17.0; recheck at implementation.

## Acceptance criteria

- [ ] The facade `__all__` contains the names selected by the retained decision
      and its refreshed per-name signature check, accounting for the current
      `APIRouter` declaration methods.
- [ ] Per-name check recorded in the PR for every removed name: placement in a
      public submodule when a public signature uses it (passed or returned),
      otherwise internal. This covers at least `call_scope`, `RpcResolverScope`,
      `RpcResolver`, `RpcResolverLike`, `RpcErrorDeclaration`, `RpcErrorMapper`,
      `RpcRejectionMapper`, `RpcRejections`, `RpcBeforeAccept`,
      `RpcValidationIssue`, `error_message`, `RpcProtocol`, `RpcServer` and
      `RpcResponseMessage`.
- [ ] `.middleware`, `.adapters` and `.errors` define `__all__` with the names
      assigned to them. `.adapters` re-exports `FastApiSocket`, `serve_websocket`,
      `RpcServer` and `RpcResponseMessage` from `adapters/fastapi.py`/`server.py`
      without importing the Dishka or OpenTelemetry adapters eagerly.
- [ ] `test_public_signature_types_are_exported` checks that each signature type
      is importable from its public path instead of from the facade.
- [ ] `__version__` and `LOGGER_NAME` leave the facade; the logger name
      `fastapi_canon.integrations.websockets` is documented.
- [ ] Optional: `ProtocolDefinitionError` subclasses
      `fastapi_canon.exceptions.ConfigurationError`, if that keeps dependency
      direction. Broadening the hierarchy is additive; record it under Changed.
- [ ] No aliases or parallel paths for removed names (integration AGENTS.md).
      Docs, examples and the bundled skill use the new paths.
- [ ] `CHANGELOG.md` `### Breaking changes` contains the WebSocket old/new import
      table. Generated clients show no drift.

## Verification

- `uv run --frozen pytest tests/unit/integrations/websockets tests/contract`
- `uv run --frozen python scripts/check.py clients` (no drift expected)
- Refresh the historical facade inventory from #102 against current consumer imports.
- Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
