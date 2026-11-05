# DX-016: Recognize bounded nested route factories in source checking

- Status: Open
- Priority: P2
- Kind: Checker coverage
- Baseline: `a366702` (2026-11-05)
- Depends on: [DX-001](DX-001-provider-factory-reassignments.md)
- Earlier review: Follow-up to #78; C3/checker coverage.

## Problem and reproduction

Inspect the reference-service source-checker output and
[the documented analysis boundaries](../docs/error-checking.md). Nested route
factories remain incomplete. Build a minimal local create_router() that creates
APIRouter, declares one nested endpoint and returns that router; the endpoint
calls a helper that raises a custom exception.

## Scope

Recognize direct, statically identifiable route factories without executing
them. Keep dynamically chosen routers, closures and arbitrary decorators
conservative; this is not general interprocedural application construction.

## Acceptance criteria

- [ ] Report each supported nested endpoint once with a stable qualified name, source line and transport.
- [ ] Resolve straightforward outer-scope error declarations and helper calls.
- [ ] Conditional/dynamic captures that cannot be established remain CAN003.
- [ ] Do not infer mounted/runtime reachability; retain the source checker's documented ownership boundary.
- [ ] Cover factories returning distinct routers and repeated declarations without cross-contaminating contracts.

## Verification

Add CLI scenarios and compare the reference-service checker report before/after; run the existing checker performance/regression tests.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
