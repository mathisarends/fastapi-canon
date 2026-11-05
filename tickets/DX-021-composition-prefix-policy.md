# DX-021: Decide whether Composition needs a prefix convenience API

- Status: Open
- Priority: P3
- Kind: Decision
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: R3.

## Problem and reproduction

A shared prefix is currently expressed through
Composition(router_factory=lambda: APIRouter(prefix="/api/v1")).
See [application router composition](../docs/composition.md).
This works; the remaining issue is ergonomics rather than missing routing.

## Scope

Evaluate only the shared prefix convenience, separately from feature-derived
namespaces/tags in DX-022 and command loading in DX-010.

## Acceptance criteria

- [ ] Compare the existing factory with an explicit prefix option using real one-feature and nested-router examples.
- [ ] Specify precedence/conflict behavior if a prefix convenience and router_factory are both supplied.
- [ ] Account for effective contract paths, router lifespans, policies, dependencies and existing app routes.
- [ ] Reject additional API surface if it does not remove meaningful repetition; document that outcome if chosen.
- [ ] Any follow-up implementation has bounded tests and a compatibility classification.

## Verification

Run current examples to verify effective paths and inspect() output; produce a small decision record without changing runtime defaults.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
