# DX-019: Create a short end-to-end getting-started path

- Status: Open
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

## Scope

Create one concise path through declaration, DI, composition, problems, checks
and a generated client. Keep detailed references authoritative and linked
rather than duplicating their option tables.

## Acceptance criteria

- [ ] A new user can build one feature with APIRouter/Inject/raises, build the app, run checks and call a generated client in a clear sequence.
- [ ] Use APIs verified in the checkout and distinguish source-only/prepared APIs from the published installation when necessary.
- [ ] Use a runnable example in examples/ as the source of the walkthrough; do not put standalone demo modules in tickets/.
- [ ] Explain only essential lifetime and error-phase rules, linking reference material for advanced transports.
- [ ] Link the path from the library overview and connect each step to one authoritative guide.
- [ ] Check commands and snippets against a clean environment; remove newly redundant prose only where authority remains clear.

## Verification

Execute the walkthrough from the repository root and inspect rendered Markdown/navigation. Run generator drift checks if an example changes.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
