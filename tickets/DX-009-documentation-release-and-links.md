# DX-009: Correct remaining release identity, links and working-directory guidance

- Status: Open
- Priority: P2
- Kind: Verified documentation bugs
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: X5 and new review findings.

## Problem and reproduction

Run:

```console
rg -n "fastapi-streams-v|current-releases|from this package directory" docs
```

At the baseline the active SSE release guide still prescribes separate
fastapi-streams tags; releases.md and compatibility.md link a removed README
section; the WebSocket dependency guide says to run from its package directory.
Use [repository migration](../docs/repository-migration.md) to distinguish active
guidance from historical evidence.

## Scope

Fix active instructions in docs/integrations/sse/releasing.md,
docs/releases.md, docs/compatibility.md and
docs/integrations/websockets/dependencies.md, plus directly associated navigation.
Keep history documents and already-published release evidence intact.

## Acceptance criteria

- [ ] Active release guidance uses the single Canon distribution and fastapi-canon-v tag prefix.
- [ ] Replace removed README anchors with existing authoritative release records or actual release links.
- [ ] Runnable development commands consistently state the repository root as their working directory.
- [ ] Inspect nearby integration indexes for misleading independent-package/workspace release wording.
- [ ] All changed local links and anchors resolve; historical predecessor names remain where they describe history.
- [ ] This documentation change does not publish, retag or rewrite historical release artifacts.

## Verification

Review each search match in context and check changed links/anchors. Run repository documentation/style gates; do not add AST policy tests.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
