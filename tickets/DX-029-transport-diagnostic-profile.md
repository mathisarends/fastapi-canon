# DX-029: Define a transport-aware composition inspection profile

- Status: Open
- Priority: P3
- Kind: Decision
- Baseline: `a366702` (2026-11-05)
- Depends on: [DX-004](https://github.com/mathisarends/fastapi-canon/pull/84), [DX-006](https://github.com/mathisarends/fastapi-canon/pull/86)
- Earlier review: Remaining C3 beyond the concrete bug tickets.

## Problem and reproduction

[System checks](../docs/system-checks.md) explicitly describe an HTTP-oriented installed snapshot. Source checking already supports HTTP/SSE/RPC/WebRTC, but this does not establish equivalent runtime inspection or checks for RPC/WebRTC/MCP. Compare inspect() output for the stack example with its exported OpenAPI/OpenRPC contracts; record which mounted operations are absent.

## Scope

Specify a small extension to the existing immutable descriptions and completeness semantics. Do not replace the separate source checker or create one catch-all transport model in this decision ticket.

## Acceptance criteria

- [ ] Inventory actual RPC, SSE, WebRTC and MCP metadata available without starting resources.
- [ ] Separate operation identity, effective path, admission failures, execution failures and dependency ownership.
- [ ] Define how supported, skipped and unsupported checks affect report completeness; do not imply universal runtime validation.
- [ ] Preserve detached snapshots, lazy optional imports, transport layer boundaries and existing JSON schema compatibility or provide an explicit migration.
- [ ] Identify one bounded first implementation slice with real reuse; retain separate protocol metadata when it carries different meaning.

## Verification

Compare real composed app snapshots and contract exports without entering lifespans. Link existing diagnostic value types and proposed versioned JSON examples.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
