# DX-015: Analyze explicit exception paths in supported context managers

- Status: Done
- Priority: P2
- Kind: Checker coverage
- Baseline: `a366702` (2026-11-05)
- Depends on: [DX-001](DX-001-provider-factory-reassignments.md)
- Earlier review: Follow-up to #78; C3/checker coverage.

## Problem and reproduction

Create a local context manager with **enter**/**exit** (and an async counterpart),
then call an explicit-error helper inside with/async with from an HTTP endpoint.
Run check errors with --strict --require-complete. On the DX-014 parent both
sync and async public-CLI probes exit 1 with only
`CAN003 unsupported statement: With/AsyncWith`; the body helper gap is missed.
After implementation they report CAN002 with a complete operation.

## Scope

Support a bounded, documented subset of local context managers and their bodies.
Handle suppression conservatively. Do not model SQLAlchemy or arbitrary external
context managers by assuming they cannot throw.

## Acceptance criteria

- [x] Follow body errors and supported enter/exit methods for local sync/async managers with meaningful traces.
- [x] Correctly handle a known nonsuppressing exit; unknown or conditional suppression remains incomplete.
- [x] Unknown external managers and unsupported protocol behavior retain CAN003 instead of becoming silently complete.
- [x] Cover normal completion and errors during enter, body and exit without importing application code.
- [x] Document the subset and demonstrate whether actual reference-service diagnostics improve.

## Verification

Use public-CLI source fixtures with real Python protocols; run all checker tests and reference-service source checking.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#97](https://github.com/mathisarends/fastapi-canon/pull/97).
- Final commit and verification: Source `4815439`; all 203 checker tests pass
  on Python 3.14 and Python 3.12, including 54 new public-CLI cases. Ruff,
  formatting, mypy, changed/full Smelt, all-files pre-commit and 77 local
  Markdown links passed. Full root pytest passed with 2,705 tests before the
  main update (26 skipped), then 2,706 tests after merging current main and
  syncing its refreshed dependencies (26 skipped, 153 warnings). The isolated
  Python 3.12 checker suite was repeated against that lockfile. Main merge
  `424346d` includes the upstream dependency stabilization; all lint, formatting,
  typing and pre-commit checks passed again.
  The reference-service scan remains exit 1: six operations, zero gaps,
  five incomplete operations; its diagnostic messages are unchanged.
- Remaining follow-ups: Inherited/decorated/generator-based protocols and
  unknown or conditional suppression remain explicitly incomplete. DX-016
  addresses the separate bounded nested route-factory slice.
