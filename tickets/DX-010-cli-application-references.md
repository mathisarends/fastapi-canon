# DX-010: Unify app and factory resolution across CLI entry points

- Status: In progress
- Priority: P2
- Kind: Feature
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: G2 and the remaining loader part of G3.

## Problem and reproduction

Compare:

```console
uv run --frozen fastapi-canon inspect --help
uv run --frozen fastapi-canon check composition --help
uv run --frozen fastapi-canon contracts export --help
```

inspect uses a positional app target and --factory; check composition targets a
Composition plus optional --app; contract export accepts an app or synchronous
factory automatically. Codegen additionally has a per-source factory flag.
See [composition CLI](../fastapi_canon/composition_cli.py),
[contract CLI](../fastapi_canon/contract_cli.py) and
[codegen readers](../fastapi_canon/codegen/readers.py).

Confirmed on the DX-009 parent through real CLI subprocesses: the same dotted
app factory accepted by contract export fails `inspect` and `check composition
--app` with exit 2. Both new factory tests reproduced that failure before the
shared loader was introduced. Command help also confirms the differing factory
selection policies. Common codegen loading actually lives in `codegen/generate.py`;
the readers consume exported documents.

## Scope

First share the app/factory loading semantics that really overlap. Keep the
Composition target distinct from an application target. Do not redesign every
command or the configuration schema in this ticket.

## Acceptance criteria

- [x] Document a consistent rule for instances, synchronous zero-argument factories, invalid/async factories and dotted attributes.
- [x] Reuse a factory result once where an invocation needs multiple contract formats.
- [x] Never enter application lifespan or resolve runtime services while loading a contract target.
- [x] Existing documented target forms continue working, or receive an explicitly versioned migration rather than disappearing silently.
- [x] Text and JSON modes produce actionable invalid-reference errors without leaking runtime configuration.
- [x] Core command help remains available without optional generator/media/telemetry runtimes.

## Verification

Extend composition CLI, contract CLI and codegen reader tests with actual temporary modules, factory-call counts and import-time failures.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#90](https://github.com/mathisarends/fastapi-canon/pull/90),
  stacked on DX-009 #89.
- Final commit and verification: implementation `ff90dd4`, shared rules/migration
  `782331e`; current `origin/main` was merged before PR creation (already current).
  Focused CLI/loader/common-codegen suites: 87 passed on Windows/Python 3.14.
  All 51 new loader cases also passed on Python 3.12. Full root pytest:
  2,573 passed, 26 skipped, 153 warnings. Mypy, Ruff, changed/full Smelt and
  all-files pre-commit passed. A fresh core-only Python 3.12 wheel successfully
  ran root, inspect, composition, contracts-export and generate help without
  importing optional generator/media/MCP/telemetry runtimes. All changed local
  Markdown links/anchors resolved. Full dependency/browser matrices were not
  repeated for target loading. Status remains In progress until merge.
- Remaining follow-ups: common codegen keeps its explicit factory selector and
  standalone contract targets; this does not redesign specialized emitters or
  configuration. JSON generation diagnostics and broader help are DX-011/DX-012.
