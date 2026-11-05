# Developer experience tickets

This backlog records the remaining DX work reviewed against `origin/main`
`a366702a8ab54c5991845bf6c0908f319225b940` on 2026-11-05, after PRs
[#77](https://github.com/mathisarends/fastapi_canon/pull/77) and
[#78](https://github.com/mathisarends/fastapi_canon/pull/78) merged.
It contains work specifications, not claims that the described fixes exist.
The prepared source API is not necessarily the currently published release.

Decision tickets are intentionally deferred and remain Open. This implementation
batch covers the other ticket kinds. Completed specifications will be removed
in a dedicated cleanup PR; their implementation PRs retain the documentation.

## Pick up one ticket

1. Read the root [AGENTS.md](../AGENTS.md), the selected ticket and the relevant
   integration instructions. Refresh `origin/main` and verify that its problem
   still exists before changing code.
2. Pick an Open ticket whose dependencies have landed. Prefer P1, then P2.
   Tickets marked Decision authorize a scoped investigation and recommendation,
   not the proposed public API or a runtime behavior change.
3. Work on one ticket per branch/PR. Update its status and this index in the
   implementation PR; link the implementation PR and final verification.
   If another change already resolved it, verify the acceptance criteria and
   close it as resolved elsewhere rather than duplicating work.
4. Reproduce current behavior through public APIs/CLI. Distinguish verified bugs
   from coverage improvements and optional design proposals. The latter include
   a current-workflow inspection, not an invented failing test.
5. Implement only the stated scope. Add follow-ups for work that needs a separate
   contract or dependency. Use the template below for new tickets.

Statuses are Open, In progress, Blocked and Done. Record a concrete blocker when
using Blocked. A Decision can finish with an explicit decision to retain current
behavior; record its rationale. Do not mark implementation work Done before the
fix and required verification have landed.

## Recommended sequence

- First fix DX-001's false complete success, then HTTP/WebSocket route collisions
  and missing endpoint dependency bindings (DX-002 through DX-005). Independent
  tickets can proceed without inventing dependencies between them.
- Close the remaining callable, SSE lookup and WebRTC factory gaps (DX-006
  through DX-008). DX-008 depends on the HTTP/SSE preflight work.
- Correct active documentation (DX-009), improve CLI loading/diagnostics/help
  (DX-010 through DX-013), and add the bounded checker coverage slices
  (DX-014 through DX-016, after DX-001).
- Provide a visible combined SDK and concise getting-started path (DX-017,
  DX-019). The remaining Decision tickets are lower priority; their output
  determines whether follow-up implementations are justified.
- DX-028 is a small independent documentation task. Do not raise dependency
  minimums merely to simplify a table.

## Ticket index

| Ticket                                              | Priority | Kind                        | Outcome                                                                  | Depends on                                                                                  | Status |
| --------------------------------------------------- | -------- | --------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- | ------ |
| [DX-001](DX-001-provider-factory-reassignments.md)  | P1       | Verified bug                | Keep provider-factory reassignment analysis sound                        | None                                                                                        | Done ([#81](https://github.com/mathisarends/fastapi-canon/pull/81)) |
| [DX-002](DX-002-http-route-collisions.md)           | P1       | Verified bug                | Reject ambiguous HTTP method and path contributions                      | None                                                                                        | Done ([#82](https://github.com/mathisarends/fastapi-canon/pull/82)) |
| [DX-003](DX-003-websocket-route-collisions.md)      | P1       | Verified bug                | Reject duplicate WebSocket paths across features                         | None                                                                                        | Done ([#83](https://github.com/mathisarends/fastapi-canon/pull/83)) |
| [DX-004](DX-004-rpc-injection-preflight.md)         | P1       | Verified coverage gap       | Validate RPC handler dependency roots during composition preflight       | None                                                                                        | Done ([#84](https://github.com/mathisarends/fastapi-canon/pull/84)) |
| [DX-005](DX-005-http-sse-injection-preflight.md)    | P1       | Coverage improvement        | Validate HTTP and SSE injected dependency roots before serving           | None                                                                                        | Done ([#85](https://github.com/mathisarends/fastapi-canon/pull/85)) |
| [DX-006](DX-006-sse-endpoint-introspection.md)      | P2       | Verified bug                | Preserve the original SSE callable for operation lookup                  | None                                                                                        | Done ([#86](https://github.com/mathisarends/fastapi-canon/pull/86)) |
| [DX-007](DX-007-callable-endpoint-injection.md)     | P2       | Verified bug                | Support injected callable objects and partial endpoints                  | None                                                                                        | Done ([#87](https://github.com/mathisarends/fastapi-canon/pull/87)) |
| [DX-008](DX-008-webrtc-factory-injection.md)        | P2       | Verified integration gap    | Make WebRTC factory injection consistent with the shared router          | [DX-005](DX-005-http-sse-injection-preflight.md)                                            | Done ([#88](https://github.com/mathisarends/fastapi-canon/pull/88)) |
| [DX-009](DX-009-documentation-release-and-links.md) | P2       | Verified documentation bugs | Correct remaining release identity, links and working-directory guidance | None                                                                                        | Done ([#89](https://github.com/mathisarends/fastapi-canon/pull/89)) |
| [DX-010](DX-010-cli-application-references.md)      | P2       | Feature                     | Unify app and factory resolution across CLI entry points                 | None                                                                                        | Done ([#90](https://github.com/mathisarends/fastapi-canon/pull/90)) |
| [DX-011](DX-011-generate-json-diagnostics.md)       | P2       | Feature                     | Add structured diagnostics to common SDK generation                      | None                                                                                        | In progress ([#91](https://github.com/mathisarends/fastapi-canon/pull/91), [#92](https://github.com/mathisarends/fastapi-canon/pull/92)) |
| [DX-012](DX-012-cli-help.md)                        | P2       | Documentation and CLI help  | Explain CLI options and exit policies in command help                    | None                                                                                        | In progress |
| [DX-013](DX-013-diagnostic-explanations.md)         | P3       | Feature                     | Provide one explain command for documented diagnostic codes              | [DX-012](DX-012-cli-help.md)                                                                | Open   |
| [DX-014](DX-014-checker-comprehensions.md)          | P2       | Checker coverage            | Analyze exception paths through comprehensions                           | [DX-001](DX-001-provider-factory-reassignments.md)                                          | Open   |
| [DX-015](DX-015-checker-context-managers.md)        | P2       | Checker coverage            | Analyze explicit exception paths in supported context managers           | [DX-001](DX-001-provider-factory-reassignments.md)                                          | Open   |
| [DX-016](DX-016-checker-nested-route-factories.md)  | P2       | Checker coverage            | Recognize bounded nested route factories in source checking              | [DX-001](DX-001-provider-factory-reassignments.md)                                          | Open   |
| [DX-017](DX-017-combined-sdk-example.md)            | P2       | Documentation and fixture   | Publish one inspectable combined SDK example                             | None                                                                                        | Open   |
| [DX-018](DX-018-multipart-support-boundary.md)      | P3       | Decision                    | Decide and slice common SDK multipart support                            | None                                                                                        | Open   |
| [DX-019](DX-019-getting-started-path.md)            | P2       | Documentation               | Create a short end-to-end getting-started path                           | [DX-009](DX-009-documentation-release-and-links.md)                                         | Open   |
| [DX-020](DX-020-mcp-activation-policy.md)           | P3       | Decision                    | Decide whether MCP mounts imply activation                               | None                                                                                        | Open   |
| [DX-021](DX-021-composition-prefix-policy.md)       | P3       | Decision                    | Decide whether Composition needs a prefix convenience API                | None                                                                                        | Open   |
| [DX-022](DX-022-feature-metadata-defaults.md)       | P3       | Decision                    | Decide ownership of feature names, tags and transport namespaces         | None                                                                                        | Open   |
| [DX-023](DX-023-public-export-organization.md)      | P3       | Decision                    | Evaluate public facade organization without arbitrary export cuts        | None                                                                                        | Open   |
| [DX-024](DX-024-generation-command-policy.md)       | P3       | Decision and documentation  | Clarify preferred and specialized generation workflows                   | None                                                                                        | Open   |
| [DX-025](DX-025-single-app-codegen-config.md)       | P3       | Decision                    | Decide a single-app shorthand for common codegen sources                 | None                                                                                        | Open   |
| [DX-026](DX-026-mcp-problem-contracts.md)           | P3       | Decision                    | Decide MCP binding of shared application problems                        | None                                                                                        | Open   |
| [DX-027](DX-027-context-terminology.md)             | P3       | Decision                    | Evaluate context terminology across connection and server APIs           | None                                                                                        | Open   |
| [DX-028](DX-028-dependency-version-overview.md)     | P3       | Documentation               | Document minimum dependencies in one current overview                    | None                                                                                        | Open   |
| [DX-029](DX-029-transport-diagnostic-profile.md)    | P3       | Decision                    | Define a transport-aware composition inspection profile                  | [DX-004](DX-004-rpc-injection-preflight.md), [DX-006](DX-006-sse-endpoint-introspection.md) | Open   |

## Shared acceptance and verification rules

Every implementation must also satisfy these rules:

- Read [architecture](../docs/architecture.md) and `smelt.yml` before changing
  boundaries or test structure. Use `uv run --frozen smelt context <path>`
  before implementation. Source/style policies belong in Smelt/Ruff.
- Verify actual APIs in the checkout. Consumer examples use documented facades;
  do not access private production members to make tests pass.
- Mirror tests under their existing roots; use real collaborators in integration
  and contract tests, finite local deadlines, and observable outcomes.
- Preserve core imports/help without optional runtimes. Do not add eager media
  imports, sibling-transport dependencies or another package workspace.
- Apply the [release policy](../AGENTS.md#release-preparation). Stricter accepted
  configurations/validation, changed defaults, public signatures and generated
  client incompatibilities require the next minor release before 1.0. At this
  baseline the published version is 0.15.0 and the next minor is 0.16.0; recheck
  current release state when implementing. Include Breaking changes migration
  notes when required; never classify work solely by its line count.
- Generate client changes through generators/templates, then verify exact drift.
  Do not edit generated output by hand.
- Run the root checks from [contributing](../docs/contributing.md) and AGENTS.md,
  plus the selected ticket's focused verification. Record commands, outcomes
  and any uncompleted checks in the implementation PR.
- No ticket authorizes publication, retagging, repository archival or a release
  workflow dispatch.

Runnable source-checker repros may be saved under ignored
`build/ticket-repros/<ticket>/src/` while investigating. Keep each source root
separate and use unique app/module names. Permanent demos belong in examples/,
shared fixtures in tests/fixtures/, and development scripts in scripts/.

## Reconciliation with the earlier 47-point review

| Earlier items | Current disposition                                                                                                                                            |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A1-A5         | Fixed. The missing-binding cases in DX-004/DX-005 are separate from the fixed automatic resolver setup.                                                        |
| E1-E4, E6     | Fixed by automatic errors, unified SSE raises and the type discriminator contract.                                                                             |
| E7            | Naming is substantially aligned; no independent implementation ticket without a remaining concrete mismatch.                                                   |
| E8            | Shared raises exists; MCP problem mapping remains DX-026. Handshake rejects has a distinct purpose.                                                            |
| D1            | HTTP/SSE endpoint injection landed. Remaining concrete gaps are DX-004/DX-005/DX-007/DX-008. FastAPI dependency functions still have explicit injection rules. |
| D2-D5, D7     | Default endpoint wiring, automatic RPC resolver, scope guidance and provider overrides are in place.                                                           |
| D6            | Evaluate the actual terminology problem in DX-027 before renaming parameters.                                                                                  |
| R1            | Main guides/examples use APIRouter. Specialized transport examples may remain when they demonstrate specialized protocols.                                     |
| R3, R4        | Optional prefix and feature-metadata defaults are DX-021/DX-022. HTTP/SSE namespace naming is already implemented.                                             |
| C1            | build() and provider overrides are implemented.                                                                                                                |
| C2-C4         | DX-020, concrete checks DX-002 through DX-008, broader profile DX-029, and explanations DX-013.                                                                |
| G1-G7         | DX-024, DX-010, DX-025, DX-012, DX-018, DX-011 and DX-017 respectively. App-based OpenRPC already exists; generated SDKs are already committed.                |
| X1-X4, X7     | Substantially fixed in central docs/examples; keep regression checks focused on remaining concrete claims.                                                     |
| X5, X6        | Active-doc corrections DX-009 and the short entry path DX-019.                                                                                                 |
| S1-S3         | Export evaluation DX-023 and current dependency overview DX-028.                                                                                               |
| S4            | Direct instrumentation=instrument(A, B) works. Explicit unpacking of differently configured groups is not itself a bug.                                        |

Deliberately deferred without an implementation ticket: inferred Error titles
(E5), a shared typed raw/binary facade (R2), and an init/scaffold command (G8).
Explicit titles and transport-specific endpoints have useful semantics; a
scaffold needs a demonstrated consumer requirement. Likewise, do not chase
arbitrary SQLAlchemy/Pydantic/hmac internals to force the source checker to claim
complete analysis. Unmodeled runtime behavior must remain visible.

## Adding another ticket

Use [TEMPLATE.md](TEMPLATE.md). Keep IDs stable, add one row here, identify
dependencies by ticket ID, and include either an executable reproduction or a
clearly labeled workflow/inspection for a proposed capability. Split decisions
from implementation when compatibility or ownership is unresolved.
