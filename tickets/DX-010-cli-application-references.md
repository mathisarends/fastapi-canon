# DX-010: Unify app and factory resolution across CLI entry points

- Status: Open
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

## Scope

First share the app/factory loading semantics that really overlap. Keep the
Composition target distinct from an application target. Do not redesign every
command or the configuration schema in this ticket.

## Acceptance criteria

- [ ] Document a consistent rule for instances, synchronous zero-argument factories, invalid/async factories and dotted attributes.
- [ ] Reuse a factory result once where an invocation needs multiple contract formats.
- [ ] Never enter application lifespan or resolve runtime services while loading a contract target.
- [ ] Existing documented target forms continue working, or receive an explicitly versioned migration rather than disappearing silently.
- [ ] Text and JSON modes produce actionable invalid-reference errors without leaking runtime configuration.
- [ ] Core command help remains available without optional generator/media/telemetry runtimes.

## Verification

Extend composition CLI, contract CLI and codegen reader tests with actual temporary modules, factory-call counts and import-time failures.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
