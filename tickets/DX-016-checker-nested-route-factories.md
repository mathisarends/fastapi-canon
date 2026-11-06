# DX-016: Recognize bounded nested route factories in source checking

- Status: Done
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

Verified on DX-015 via the public CLI: two direct factories, each declaring a
local endpoint, produce only CAN003 `nested route declarations require runtime
factory execution`. Their helper error is not compared with local contracts.
Afterward the declared factory is complete and the unmapped factory reports
CAN002, with independent factory-qualified names and a trace to its helper.

## Scope

Recognize direct, statically identifiable route factories without executing
them. Keep dynamically chosen routers, closures and arbitrary decorators
conservative; this is not general interprocedural application construction.

## Acceptance criteria

- [x] Report each supported nested endpoint once with a stable qualified name, source line and transport.
- [x] Resolve straightforward outer-scope error declarations and helper calls.
- [x] Conditional/dynamic captures that cannot be established remain CAN003.
- [x] Do not infer mounted/runtime reachability; retain the source checker's documented ownership boundary.
- [x] Cover factories returning distinct routers and repeated declarations without cross-contaminating contracts.

## Verification

Add CLI scenarios and compare the reference-service checker report before/after; run the existing checker performance/regression tests.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#98](https://github.com/mathisarends/fastapi-canon/pull/98); merged as `1f8bf0b`.
- Final commit and verification: Source `0347678`; all 232 checker tests pass
  on Python 3.14 and Python 3.12, including 29 new CLI cases and an updated
  formerly-unsupported factory regression. A 100-factory scenario verifies
  independent summaries, qualified names and complete contract-gap reporting.
  Ruff, formatting, mypy, changed/full Smelt and all-files pre-commit pass.
  Full root pytest: 2,735 passed, 26 skipped, 153 warnings.
  Reference-service results and messages remain unchanged: six operations,
  zero gaps, five incomplete operations, strict/require-complete exit 1.
- Remaining follow-ups: General closures, dynamic setup/captures, async factories
  and app/Feature-returning telemetry factories remain explicitly incomplete.
  Source ownership still does not establish mounted runtime reachability.
