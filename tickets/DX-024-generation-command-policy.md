# DX-024: Clarify preferred and specialized generation workflows

- Status: Open
- Priority: P3
- Kind: Decision and documentation
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: G1.

## Problem and reproduction

Root help lists contracts export, generate, sse, websockets and webrtc.
Specialized emitters retain extra runtime/configuration options and their own
ownership manifests. The [public API guide](../docs/canon.md) already recommends
common generate but still documents every family.

## Scope

Document a workflow/compatibility matrix and decide what can become advanced
documentation. Do not remove commands, hide working options or merge ownership
manifests as incidental cleanup.

## Acceptance criteria

- [ ] Show when to use contracts export, common generate and each specialized emitter.
- [ ] Inventory specialized capabilities without a common equivalent before proposing deprecation.
- [ ] Define any migration prerequisites, config/output ownership implications and version impact.
- [ ] Give new users one preferred documented path while preserving supported advanced workflows.
- [ ] Split approved removal/compatibility work into follow-up tickets; a documentation-only recommendation may close this decision.

## Verification

Run each help entry and use at least one real config per specialized workflow to verify the matrix.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
