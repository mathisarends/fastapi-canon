# DX-019: Create a short end-to-end getting-started path

- Status: In progress
- Priority: P2
- Kind: Documentation
- Baseline: `a366702` (2026-11-05)
- Depends on: [DX-009](DX-009-documentation-release-and-links.md)
- Earlier review: X6; X1-X4 are largely completed.

## Problem and reproduction

A newcomer currently moves between README, canon.md, composition.md, routers.md,
problems.md, contracts.md and the codegen guide. CONCEPTS.md was already removed,
and central examples already use APIRouter; do not repeat those completed edits.
Follow the actual links from README and record the shortest current path.

The pre-change shortest route was README's core section to the composition guide
for providers/DI, then README's SDK section to the generation guide. Showcase's
README ended at manual OpenAPI export and a stale removed CONCEPTS link. No one
page connected its existing feature and provider to source checks and an actual
SDK consumer. The new README link reaches that path in one step.

## Scope

Create one concise path through declaration, DI, composition, problems, checks
and a generated client. Keep detailed references authoritative and linked
rather than duplicating their option tables.

## Acceptance criteria

- [x] A new user can build one feature with APIRouter/Inject/raises, build the app, run checks and call a generated client in a clear sequence.
- [x] Use APIs verified in the checkout and distinguish source-only/prepared APIs from the published installation when necessary.
- [x] Use a runnable example in examples/ as the source of the walkthrough; do not put standalone demo modules in tickets/.
- [x] Explain only essential lifetime and error-phase rules, linking reference material for advanced transports.
- [x] Link the path from the library overview and connect each step to one authoritative guide.
- [x] Check commands and snippets against a clean environment; remove newly redundant prose only where authority remains clear.

## Verification

Execute the walkthrough from the repository root and inspect rendered Markdown/navigation. Run generator drift checks if an example changes.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: Pending publication after completed checks.
- Final commit and verification: Runnable path `fbeff91`; full root pytest
  2,742 passed, 26 skipped, 189 warnings. All three new real CLI/generation/HTTP
  scenarios also pass in an isolated Python 3.12 environment. Source checking
  covers two catalog declarations with zero gaps/incomplete results; composition
  preflight passes all checks and inspection describes all three mounted routes.
  Live/offline exact generation checks and saved-contract byte drift pass.
  Root mypy and all 12 showcase modules pass; Ruff, formatting, changed/full
  Smelt and pre-commit pass. All 202 changed local Markdown links resolve.
  Rendered Markdown has six headings, seven code blocks and 22 navigation links.
  The worktree environment was freshly synchronized from the root frozen lockfile.
- Remaining follow-ups: The broader showcase-root source scan still retains
  CAN003 for facade re-exports. The guide explicitly scopes source checking to
  the catalog and uses composition/runtime checks for the full mounted app.
  No checker rule, runtime library API, dependency minimum or release changes.
