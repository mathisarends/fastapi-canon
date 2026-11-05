# DX-027: Evaluate context terminology across connection and server APIs

- Status: Open
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

- [ ] Show both current patterns with complete runnable examples and identify a concrete confusing use case.
- [ ] Distinguish context factories, hook results, injected services and standalone server values.
- [ ] Compare a documentation fix with a parameter rename and account for every affected facade/example.
- [ ] If a rename is recommended, follow the WebSocket compatibility rules and specify a next-minor migration.
- [ ] Record a concrete decision; no rename is required merely because the same word appears twice.

## Verification

Verify the current signatures and run the existing context/hook scenarios; check guide links.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
