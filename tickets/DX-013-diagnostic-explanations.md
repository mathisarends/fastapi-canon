# DX-013: Provide one explain command for documented diagnostic codes

- Status: In progress
- Priority: P3
- Kind: Feature
- Baseline: `a366702` (2026-11-05)
- Depends on: [DX-012](DX-012-cli-help.md)
- Earlier review: C4.

## Problem and reproduction

The source checker emits CAN001-CAN003; composition emits canon.* codes.
`uv run --frozen fastapi-canon explain CAN003` is not a supported command.
The code families are documented separately in
[error checking](../docs/error-checking.md) and
[system checks](../docs/system-checks.md).

Reproduced on the DX-012 parent: the installed command rejects `explain` as an
invalid subcommand and exits 2. The offline catalogue covers the three source
codes, all sixteen documented composition codes and the five common generation
codes introduced by DX-011. Existing emitters and their code names are unchanged.

## Scope

Add a small diagnostic explanation entry point for existing codes. Do not
renumber public codes simply to make the two families look alike.

## Acceptance criteria

- [x] Both code families resolve to explanations with meaning, a minimal example, a repair and relevant guide links.
- [x] Unknown codes fail with an actionable message and stable nonzero exit status.
- [x] Help/explanation does not import an application or optional runtime.
- [x] Document whether machine-readable explanation output is supported; keep codes emitted by current checks compatible.
- [x] Keep diagnostic data owned by the existing layer boundaries; do not make static analysis import composition runtimes.

## Verification

Exercise the public command for CAN001, CAN003, canon.routing.E002 and an unknown code; run optional-import and CLI checks.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification: implementation `54feb8c`. All 24 built-in codes
  have JSON meaning/example/repair/guide coverage; text, unknown/custom codes,
  help and optional-import checks also pass (51 focused unit cases). Combined
  CLI/composition/generation coverage: 70 passed. Full root pytest: 2,619 passed,
  26 skipped, 153 warnings. A fresh Python 3.12 core-only wheel passed explanation
  and help for source, routing, provider and generation codes without optional
  runtimes. Mypy, Ruff, changed/full Smelt and all-files pre-commit passed.
  All 175 changed local Markdown links/anchors resolve, including the new guide.
  Client/browser/dependency matrices were not repeated for static explanation
  lookup. DX-011's follow-up and DX-012 are now confirmed merged. Status remains
  In progress until merge.
- Remaining follow-ups: application-defined codes use their own finding hint;
  lookup does not execute application checks or prove their runtime coverage.
  Decision tickets remain intentionally Open and untouched by implementation.
