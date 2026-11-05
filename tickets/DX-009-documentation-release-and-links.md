# DX-009: Correct remaining release identity, links and working-directory guidance

- Status: Done
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

Confirmed on the DX-008 parent: the search finds four active matches (two dead
README anchors, the SSE tag instruction and the WebSocket working directory)
plus one valid historical release link. After correction only the historical
`fastapi-streams-v0.5.0` release link remains.

## Scope

Fix active instructions in docs/integrations/sse/releasing.md,
docs/releases.md, docs/compatibility.md and
docs/integrations/websockets/dependencies.md, plus directly associated navigation.
Keep history documents and already-published release evidence intact.

## Acceptance criteria

- [x] Active release guidance uses the single Canon distribution and fastapi-canon-v tag prefix.
- [x] Replace removed README anchors with existing authoritative release records or actual release links.
- [x] Runnable development commands consistently state the repository root as their working directory.
- [x] Inspect nearby integration indexes for misleading independent-package/workspace release wording.
- [x] All changed local links and anchors resolve; historical predecessor names remain where they describe history.
- [x] This documentation change does not publish, retag or rewrite historical release artifacts.

## Verification

Review each search match in context and check changed links/anchors. Run repository documentation/style gates; do not add AST policy tests.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#89](https://github.com/mathisarends/fastapi-canon/pull/89), stacked on DX-008 #88.
- Final commit and verification: documentation `61ef765`. Checked all 116 local
  links/heading anchors in the nine changed guides; all resolve. Imported the
  authenticated WebSocket demo from the root successfully. Compared installed
  SSE command and benchmark source-directory guidance with their actual scripts.
  Search results now contain only the valid historical Streams tag. Full
  pre-commit passed Smelt and lint/format policy. Runtime tests and publication
  matrices were not repeated for prose/link changes; no runtime code changed.
  Historical changelogs and published release evidence were preserved. Merged into main as `3d56f54`.
- Remaining follow-ups: the dependency overview is separately scoped in DX-028;
  this ticket does not change dependency minimums or published versions.
