# DX-011: Add structured diagnostics to common SDK generation

- Status: In progress
- Priority: P2
- Kind: Feature
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: G6.

## Problem and reproduction

Run `uv run --frozen fastapi-canon generate --help`.
The common command has no JSON output mode. Errors in
[the CLI](../fastapi_canon/codegen/cli.py) are flattened to stderr text, while the
[codegen guide](../docs/unified-canon-concept.md) lists structured diagnostics as
future work. Use the existing canonical_errors configuration as a real fixture.

Confirmed on the DX-010 parent: public generate help has no output-format option.
The new JSON cases use a copied canonical_errors app/configuration with real
HTTP/SSE and RPC sources, plus saved empty/unsupported contracts. They cover
success, clean/drift checks, diff, source warnings and failures before any artifact
write, including app output that must not contaminate machine-readable stdout.

## Scope

Define and implement a versioned JSON diagnostic envelope for common generate,
including successful generation, read-only drift checks and preflight errors.
Do not change specialized emitters or build a CI-platform-specific uploader.

## Acceptance criteria

- [x] Expose a documented JSON mode with stable severity/code/message fields and source/operation locations when available.
- [x] Machine output is parseable with no progress text mixed into stdout; text mode retains readable diagnostics.
- [x] Preserve documented exit behavior for success, drift and invalid configuration.
- [x] Generation errors and drift in check/diff mode do not partially write contracts or SDK files.
- [x] Report unsupported operations and selected target context without exposing secrets.
- [x] Document examples showing how CI can consume the JSON fields; test schema_version and empty/success cases.

## Verification

Exercise real configurations and saved contracts through the public CLI; run codegen planner/render/drift tests.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#91](https://github.com/mathisarends/fastapi-canon/pull/91),
  merged as `9af03f3`; final installation-hint/documentation follow-up
  [#92](https://github.com/mathisarends/fastapi-canon/pull/92).
- Final commit and verification: implementation `e5e5b30`, merged main through
  DX-010 in `887ff97`, installed-core dependency-hint follow-up `b140ceb`.
  Focused CLI/planner/render/consumer suites: 150 passed. All eight new JSON
  scenarios passed on Python 3.12 and 3.14; the final focused rerun also passed.
  Full root pytest: 2,581 passed, 26 skipped, 153 warnings. The complete client
  gate passed exact drift, TypeScript types, runtime and coverage checks.
  Mypy, Ruff, changed/full Smelt and all-files pre-commit passed. A fresh Python
  3.12 wheel installation passed the complete core installed-artifact smoke,
  including JSON diagnostics with the optional codegen dependencies absent.
  The smoke is now permanently part of the wheel matrix. All changed local
  Markdown links/anchors resolve. Browser and all-combination dependency
  matrices were not repeated for diagnostic output. Status remains In progress
  until merge.
- Remaining follow-ups: no specialized-emitter or CI-uploader redesign is
  included. Locations are optional for global/foreign failures; owned source
  and operation failures carry available metadata. CLI help and code
  explanations are DX-012/DX-013. DX-007 through DX-010 are now marked Done with
  their confirmed main merge commits.
