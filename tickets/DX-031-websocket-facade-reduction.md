# DX-031: Split the WebSocket facade by user task

- Status: Open
- Priority: P2
- Kind: Feature (breaking public API)
- Baseline: `df05e84` (2026-11-06)
- Depends on: [DX-023](DX-023-public-export-organization.md)
- Earlier review: S2

## Problem and reproduction

Current workflow inspection, not a bug:

```console
uv run --frozen python -c "import fastapi_canon.integrations.websockets as ws; print(len(ws.__all__))"
```

The facade exports 75 names; 48 are never imported by documented consumers
(see the [DX-023 inventory](DX-023-public-export-organization.md#inventory)).
Channel and socket declarations share autocomplete with JSON-RPC envelopes,
endpoint and resolver internals, middleware authoring types, framework adapter
functions and protocol error classes.

## Scope

Apply the [WebSocket decision](DX-023-public-export-organization.md#websocket-facade):
the facade keeps the endpoint-building names; middleware authoring moves to
`.middleware`, framework adapters to `.adapters`, protocol errors to `.errors`;
other names become internal unless a public signature uses them.

Entry points: [`websockets/__init__.py`](../fastapi_canon/integrations/websockets/__init__.py),
`websockets/adapters/__init__.py` (currently empty),
[`test_public_api.py`](../tests/unit/integrations/websockets/test_public_api.py),
`docs/integrations/websockets/`, `docs/canon.md`, `examples/websockets/`,
`docs/telemetry.md` and the integration [AGENTS.md](../fastapi_canon/integrations/websockets/AGENTS.md).

Out of scope: wire behavior, OpenRPC output and generated clients. Moving
`RpcChannel` out of the root is DX-030.

## Acceptance criteria

- [ ] The facade `__all__` contains the kept names from DX-023, plus any passed
      type the per-name check below assigns to it.
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
- Rerun the DX-023 inventory for the facade.
- Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
