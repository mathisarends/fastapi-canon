# DX-011: Add structured diagnostics to common SDK generation

- Status: Open
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

## Scope

Define and implement a versioned JSON diagnostic envelope for common generate,
including successful generation, read-only drift checks and preflight errors.
Do not change specialized emitters or build a CI-platform-specific uploader.

## Acceptance criteria

- [ ] Expose a documented JSON mode with stable severity/code/message fields and source/operation locations when available.
- [ ] Machine output is parseable with no progress text mixed into stdout; text mode retains readable diagnostics.
- [ ] Preserve documented exit behavior for success, drift and invalid configuration.
- [ ] Generation errors and drift in check/diff mode do not partially write contracts or SDK files.
- [ ] Report unsupported operations and selected target context without exposing secrets.
- [ ] Document examples showing how CI can consume the JSON fields; test schema_version and empty/success cases.

## Verification

Exercise real configurations and saved contracts through the public CLI; run codegen planner/render/drift tests.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
