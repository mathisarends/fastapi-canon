# DX-024: Clarify preferred and specialized generation workflows

- Status: Done
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

- [x] Show when to use contracts export, common generate and each specialized emitter.
- [x] Inventory specialized capabilities without a common equivalent before proposing deprecation.
- [x] Define any migration prerequisites, config/output ownership implications and version impact.
- [x] Give new users one preferred documented path while preserving supported advanced workflows.
- [x] Split approved removal/compatibility work into follow-up tickets; a documentation-only recommendation may close this decision.

## Verification

Run each help entry and use at least one real config per specialized workflow to verify the matrix.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Decision

**Decision: documentation only. Recommend `fastapi-canon generate` as the one
documented path and keep every specialized command.** No command, option or
ownership manifest is removed, hidden or merged.

The [public API guide](../docs/canon.md#choosing-a-generation-command) now has a
workflow matrix: `generate` for a combined SDK, `contracts export` for contract
files, and `sse`, `websockets` and `webrtc` only for the capabilities that have no
common equivalent. Inventory taken from each command's `--help`:

- **SSE:** accepted-diagnostics control (`--strict`, `--allow-diagnostics`,
  `--diagnostics`), `--python-http-client`, saved-schema `check --app` comparison
  and `sync` over `[tool.fastapi-canon.sse]`.
- **WebSockets:** generation from an `RpcChannel` or `RpcContract` reference
  without a FastAPI app, `--server`, socket-protocol `--binding`,
  `--transport-module`, `--api-root`, `--api-name` and a multi-client `--config`
  manifest.
- **WebRTC:** a TypeScript-only client from a saved or live contract.
- **Common `generate`:** per-source `names`, `identities` and operation
  include/exclude lists cover renaming and filtering, but not the options above.

Because those options have no equivalent, no deprecation is proposed. A directory
should be owned by a single workflow because the families keep separate ownership
manifests. Any future removal must first provide common equivalents, a migration
for manifests and outputs, and a next-minor `### Breaking changes` entry.

## Completion

- Implementation or decision PR: PR_LINK
- Final commit and verification: documentation only. Every command's `--help` was run and compared with the
matrix. `fastapi-canon contracts export --app app:app` wrote `openapi.json` and
`openrpc.json` for `examples/canonical_errors`; `fastapi-canon generate --check`
there reported drift with exit code 1 because `dist/` is not generated in a clean
checkout, matching the documented exit codes. A real configuration for each
specialized family was not executed in this change.
- Remaining follow-ups: none required. Real-config verification of the SSE, WebSocket and WebRTC columns can accompany any future deprecation proposal.
