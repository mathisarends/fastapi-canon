# DX-012: Explain CLI options and exit policies in command help

- Status: Done
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

Inspected the parent help/adapters after DX-010/DX-011: generate target selection
and composition format/threshold/completeness options still lacked explanations.
The updated help retains automatic app factory loading, the compatibility
`--factory` flag and the new versioned JSON generation mode. Parser choices,
defaults and dispatch stay unchanged.

## Scope

Improve help in [CLI adapters](../fastapi_canon/cli.py),
[composition CLI](../fastapi_canon/composition_cli.py),
[source-check CLI](../fastapi_canon/check_error_contracts.py) and
[generate CLI](../fastapi_canon/codegen/cli.py), without changing parsing semantics.

## Acceptance criteria

- [x] Explain accepted target syntax, defaults, repeatable --target selection, output modes and failure thresholds.
- [x] Explain --strict versus --require-complete without implying full runtime exception coverage.
- [x] State when --diff implies --check and whether a command imports/calls app code.
- [x] Use installed fastapi-canon command names in usage and error output.
- [x] Keep help importable without optional runtime/generator dependencies.
- [x] Update help when DX-010/DX-011 land; existing command syntax remains accepted.

## Verification

Run each public --help command and the existing CLI tests. Prefer semantic help assertions to whole-output snapshots.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#93](https://github.com/mathisarends/fastapi-canon/pull/93).
- Final commit and verification: help/semantic coverage `49c38b5`. Focused public
  CLI, loader, contract and generation suites: 95 passed. Full root pytest:
  2,586 passed, 26 skipped, 153 warnings on Windows/Python 3.14. A fresh Python
  3.12 core-only wheel passed root, generate, inspect, composition and source
  checker help without importing optional generator/media/MCP/telemetry
  runtimes. Mypy, Ruff, changed/full Smelt and all-files pre-commit passed.
  Changed Markdown links/anchors resolve. Argument choices, defaults and
  dispatch were not redesigned; no generator/client drift or browser matrix
  was repeated for help text. Merged into main as `e1e105e`.
- Remaining follow-ups: diagnostic explanation lookup is separately scoped in
  DX-013. Documentation retains the module source-check entry point alongside
  the installed `fastapi-canon check errors` command.
