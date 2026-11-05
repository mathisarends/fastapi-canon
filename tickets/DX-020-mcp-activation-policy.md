# DX-020: Decide whether MCP mounts imply activation

- Status: Open
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

- [ ] Compare keeping explicit activation with lazy default activation for present mounts.
- [ ] Specify default path/auth/stateless behavior, an explicit disable mechanism if needed and route-collision handling.
- [ ] Core imports/help and applications without MCP must remain free of FastMCP runtime requirements.
- [ ] Record security/ownership implications of exposing an endpoint automatically and version any changed default under the release policy.
- [ ] Finish with a documented recommendation; implement a behavior change only in a separately scoped follow-up.

## Verification

Verify current behavior with real native MCP declarations and existing composition checks; cite the relevant runtime tests.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
