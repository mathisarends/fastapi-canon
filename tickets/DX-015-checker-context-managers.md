# DX-015: Analyze explicit exception paths in supported context managers

- Status: Open
- Priority: P2
- Kind: Checker coverage
- Baseline: `a366702` (2026-11-05)
- Depends on: [DX-001](DX-001-provider-factory-reassignments.md)
- Earlier review: Follow-up to #78; C3/checker coverage.

## Problem and reproduction

Create a local context manager with **enter**/**exit** (and an async counterpart),
then call an explicit-error helper inside with/async with from an HTTP endpoint.
Run check errors with --strict --require-complete. Context managers are outside
the baseline's supported flow analysis; record current CAN003 output first.

## Scope

Support a bounded, documented subset of local context managers and their bodies.
Handle suppression conservatively. Do not model SQLAlchemy or arbitrary external
context managers by assuming they cannot throw.

## Acceptance criteria

- [ ] Follow body errors and supported enter/exit methods for local sync/async managers with meaningful traces.
- [ ] Correctly handle a known nonsuppressing exit; unknown or conditional suppression remains incomplete.
- [ ] Unknown external managers and unsupported protocol behavior retain CAN003 instead of becoming silently complete.
- [ ] Cover normal completion and errors during enter, body and exit without importing application code.
- [ ] Document the subset and demonstrate whether actual reference-service diagnostics improve.

## Verification

Use public-CLI source fixtures with real Python protocols; run all checker tests and reference-service source checking.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
