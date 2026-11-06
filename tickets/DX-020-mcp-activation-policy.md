# DX-020: Decide whether MCP mounts imply activation

- Status: Done
- Priority: P3
- Kind: Decision
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: C2.

## Problem and reproduction

Feature(mcp=[McpMount(...)]) still requires Composition(mcp=McpOptions()).
[validate_mounts](../fastapi_canon/integrations/mcp/runtime.py) deliberately reports
an error without that option. Build a minimal native McpServer/McpMount using
the [MCP guide](../docs/integrations/mcp/README.md) to compare explicit options,
mounts without options and an app without MCP.

## Scope

Decide whether the second activation is useful application-boundary policy or
avoidable repetition. Do not silently choose a new default endpoint in this
decision ticket.

## Acceptance criteria

- [x] Compare keeping explicit activation with lazy default activation for present mounts.
- [x] Specify default path/auth/stateless behavior, an explicit disable mechanism if needed and route-collision handling.
- [x] Core imports/help and applications without MCP must remain free of FastMCP runtime requirements.
- [x] Record security/ownership implications of exposing an endpoint automatically and version any changed default under the release policy.
- [x] Finish with a documented recommendation; implement a behavior change only in a separately scoped follow-up.

## Verification

Verify current behavior with real native MCP declarations and existing composition checks; cite the relevant runtime tests.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Decision

**Decision: keep explicit activation.** `Feature(mcp=[McpMount(...)])` stays a
declaration of what a feature contributes; `Composition(mcp=McpOptions())` stays
the single place that decides whether the application exposes an MCP endpoint.

| | Explicit activation (current) | Lazy default activation |
| --- | --- | --- |
| Endpoint exposure | Only when the application opts in | Any feature mount opens `/mcp` |
| Authentication | Always visible next to `auth=`/`auth_factory=` | Default would be no auth, because child servers cannot carry auth |
| Repetition | One `mcp=McpOptions()` line per application | None |
| Path, name, stateless and masking options | Declared once on `McpOptions` | Need an implicit default set that is easy to miss |
| Reusable features | A feature can ship mounts without deciding exposure | A reused feature would silently expose an endpoint in every app |

The only saving is one line. The cost would be an unauthenticated endpoint that a
feature author, not the application owner, can open. Requirements that any later
proposal must meet: default path `/mcp` and server name from `FastAPI.title`
(unchanged), `stateless_http` and `mask_error_details` remain `None`, a default
authentication provider is not acceptable, an explicit disable switch would be
needed so a reused feature can be mounted without exposure, and path collisions
keep failing preflight as `canon.mcp.E001`. Changing the default would be a changed
default and a behavior change under the release policy, so it requires the next
minor release with a `### Breaking changes` entry.

Core imports and applications without MCP are unaffected because no code changes:
FastMCP is still imported only when `McpOptions` is used.

The MCP guide now explains why activation is explicit and shows the error text
with its repair.

## Completion

- Implementation or decision PR: [#99](https://github.com/mathisarends/fastapi-canon/pull/99)
- Final commit and verification: documentation only. `tests/integration/integrations/mcp/test_runtime.py`
(33 passed) includes `test_mounts_require_explicit_composition_options`, which
pins the current behavior, and `test_native_fastmcp_tools_need_no_providers` for
the opted-in path. No runtime, signature or default changed.
- Remaining follow-ups: none required. A coded diagnostic and `fastapi-canon explain` entry for the
missing-`McpOptions` error is optional because the message already names the repair.
