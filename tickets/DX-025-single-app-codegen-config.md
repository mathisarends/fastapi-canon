# DX-025: Decide a single-app shorthand for common codegen sources

- Status: Open
- Priority: P3
- Kind: Decision
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: Remaining G3.

## Problem and reproduction

[codegen.sources.api] and [codegen.sources.control] can already reference the
same app; an extra OpenRPC contract object is no longer required. Each source
still repeats kind/source/output settings, and no [codegen.app] shorthand exists.
Inspect [configuration](../fastapi_canon/codegen/config.py),
[readers](../fastapi_canon/codegen/readers.py) and
[the guide](../docs/unified-canon-concept.md).

## Scope

Evaluate a shorthand for the existing multi-source model. This is separate from
DX-010's reference-loader semantics and DX-024's command lifecycle policy.

## Acceptance criteria

- [ ] Compare the current configuration with a proposed app block for HTTP/SSE/RPC/WebRTC and multi-app projects.
- [ ] Specify output filenames, target source selection, factory caching and offline saved-contract behavior.
- [ ] Define conflicts with explicit sources; do not erase existing per-source overrides or identity mappings.
- [ ] Keep the current schema valid unless a deliberate breaking migration is proposed.
- [ ] Record a concrete recommendation and bounded follow-ups, or document why the current explicit model should remain.

## Verification

Use canonical_errors and a multi-source config to demonstrate both forms conceptually; verify every current key against the parser.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
