# DX-014: Analyze exception paths through comprehensions

- Status: In progress
- Priority: P2
- Kind: Checker coverage
- Baseline: `a366702` (2026-11-05)
- Depends on: [DX-001](DX-001-provider-factory-reassignments.md)
- Earlier review: Follow-up to #78; C3/checker coverage.

## Problem and reproduction

Save an importable source module with APIRouter and an endpoint returning
`[require_item(item_id) for item_id in item_ids]`; require_item explicitly raises
a custom Missing exception. Run check errors with --strict --require-complete.
At baseline comprehensions are documented as unsupported and prevent complete
analysis. Establish exact diagnostics for list/set/dict/generator forms before
editing [analysis.py](../fastapi_canon/checks/analysis.py).

Verified on the DX-013 parent through the public CLI: all four forms exit 1 with
`CAN002` plus `CAN003 unsupported expression: ListComp/SetComp/DictComp/GeneratorExp`,
and `complete=false`. The generator result is a false executed-body claim at
creation time. After the change, the eager forms report only CAN002 and are
complete; an unconsumed generator reports deferred CAN003 without body gaps.

## Scope

Support eagerly executed list/set/dict comprehensions first. Keep deferred
generator expressions explicit; do not claim they execute merely when created.

## Acceptance criteria

- [x] Follow exceptions from iterable, filters and result expressions of eager comprehensions and preserve source call paths.
- [x] Cover nested and async eager comprehensions where their supported execution model is clear; retain CAN003 for unsupported forms.
- [x] A constructed but unconsumed generator expression does not count as executed error coverage.
- [x] Caught exceptions, mappings and missing raises still yield the correct CAN001/CAN002 distinction.
- [x] Document exactly which forms are complete; add a before/after example from the reference service where applicable.

## Verification

Add public-CLI regression scenarios under the existing checks test root, then run its full suite and the reference-service source checker.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: Pending publication after verification.
- Final commit and verification: Implementation `8d923e4`; 2,651 root tests
  passed (26 skipped); all 149 checker tests also passed on Python 3.12.
  Ruff, formatting, mypy, changed/full Smelt and all pre-commit hooks passed.
  Public CLI before/after probes cover list/set/dict/generator forms.
  Reference-service checking remains exit 1 with six operations, no gaps and
  five incomplete operations; two unsupported ListComp diagnostics disappeared.
- Remaining follow-ups: Context managers and nested route factories remain
  scoped to DX-015 and DX-016. Generator aliases, arbitrary iteration protocols
  and element-type dispatch remain explicitly incomplete.
