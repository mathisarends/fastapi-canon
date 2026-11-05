# DX-012: Explain CLI options and exit policies in command help

- Status: Open
- Priority: P2
- Kind: Documentation and CLI help
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: G4; A2 is already closed.

## Problem and reproduction

Run help for generate, inspect, check composition and check errors.
Options including generate --target and composition --format, --fail-on and
--require-complete have little or no explanatory help. The earlier broken
check-errors subcommand dispatch is already fixed; do not reimplement it.

## Scope

Improve help in [CLI adapters](../fastapi_canon/cli.py),
[composition CLI](../fastapi_canon/composition_cli.py),
[source-check CLI](../fastapi_canon/check_error_contracts.py) and
[generate CLI](../fastapi_canon/codegen/cli.py), without changing parsing semantics.

## Acceptance criteria

- [ ] Explain accepted target syntax, defaults, repeatable --target selection, output modes and failure thresholds.
- [ ] Explain --strict versus --require-complete without implying full runtime exception coverage.
- [ ] State when --diff implies --check and whether a command imports/calls app code.
- [ ] Use installed fastapi-canon command names in usage and error output.
- [ ] Keep help importable without optional runtime/generator dependencies.
- [ ] Update help when DX-010/DX-011 land; existing command syntax remains accepted.

## Verification

Run each public --help command and the existing CLI tests. Prefer semantic help assertions to whole-output snapshots.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
