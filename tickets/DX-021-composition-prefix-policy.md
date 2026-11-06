# DX-021: Decide whether Composition needs a prefix convenience API

- Status: Done
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

- [x] Compare the existing factory with an explicit prefix option using real one-feature and nested-router examples.
- [x] Specify precedence/conflict behavior if a prefix convenience and router_factory are both supplied.
- [x] Account for effective contract paths, router lifespans, policies, dependencies and existing app routes.
- [x] Reject additional API surface if it does not remove meaningful repetition; document that outcome if chosen.
- [x] Any follow-up implementation has bounded tests and a compatibility classification.

## Verification

Run current examples to verify effective paths and inspect() output; produce a small decision record without changing runtime defaults.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Decision

**Decision: reject a `Composition(prefix=...)` option; keep `router_factory`.**

Checked with a one-feature app whose router contains a nested router (both
`APIRouter` with namespaces), built without a factory and with
`router_factory=lambda: APIRouter(prefix="/api/v1")`:

| | `Composition.inspect(app)` and OpenAPI paths | Operation IDs |
| --- | --- | --- |
| No factory | `/projects/{project_id}`, `/projects/{project_id}/tasks` | `projects.get_project`, `tasks.list_tasks` |
| Factory with prefix | `/api/v1/projects/{project_id}`, `/api/v1/projects/{project_id}/tasks` | unchanged |

The factory already covers one-feature and nested-router cases with the effective
path visible in `inspect()` and the contract export. A `prefix=` option would only
save the `lambda: APIRouter(...)` wrapper, but it would add API surface that needs
rules for combination with `router_factory` (error, or which one wins), for the
factory's `tags`/`dependencies` that users already put in the same call, and for
router lifespans and policies owned by the factory. Because the repetition removed
is a few characters, the extra surface is not justified. If it were added, supplying
both `prefix` and `router_factory` should be rejected rather than merged, and it
would be an additive, non-breaking change.

The composition guide now states the one-line recipe and the verified effects on
paths and operation IDs.

## Completion

- Implementation or decision PR: [#99](https://github.com/mathisarends/fastapi-canon/pull/99)
- Final commit and verification: documentation only. A throwaway script built the same feature with and without
the factory and printed `inspect()` operations and `app.openapi()` paths (results
in the table above). No runtime, signature or default changed.
- Remaining follow-ups: none.
