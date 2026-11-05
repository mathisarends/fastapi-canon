# DX-023: Evaluate public facade organization without arbitrary export cuts

- Status: Open
- Priority: P3
- Kind: Decision
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: S1/S2; defer implementation until its benefit is established.

## Problem and reproduction

Inspect [root exports](../fastapi_canon/__init__.py) and
[WebSocket exports](../fastapi_canon/integrations/websockets/__init__.py).
Both include everyday APIs and advanced protocol/diagnostic types.
The diagnostics module already exists; its mere existence does not remove root
re-exports. Export count alone is not evidence of a defect.

## Scope

Inventory real consumer imports and document an organization proposal.
Do not move public types, create a new workspace or remove exports in this ticket.

## Acceptance criteria

- [ ] Classify actual exports by user task and identify concrete autocomplete/discoverability problems.
- [ ] Reuse existing public namespaces when they fit; verify any proposed new namespace against architecture rules.
- [ ] Include documented imports, examples, bundled skill and generated-consumer impacts.
- [ ] Specify a migration consistent with root and integration AGENTS.md; do not assume deprecation shims are permitted everywhere.
- [ ] Preserve lazy optional imports and one Canon version.
- [ ] Conclude with a recommendation; declining an export reduction is a valid outcome.

## Verification

Verify inventory against the actual facades and search consumer imports with rg. Link the proposed documentation/import examples.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
