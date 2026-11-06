# DX-026: Decide MCP binding of shared application problems

- Status: Done
- Priority: P3
- Kind: Decision
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: Remaining E8; raises versus rejects is intentionally phase-specific.

## Problem and reproduction

HTTP/SSE/RPC already share Error declarations through raises=.
MCP tools are registered through the
[MCP facade](../fastapi_canon/integrations/mcp/__init__.py) and do not expose the
same canonical problem binding. RPC rejects= describes handshake rejection,
which is a different phase and is not automatically a naming defect.

## Scope

Design only how known application errors should appear in MCP tools/resources
and inspection. Do not rename handshake APIs or force every transport into an
HTTP envelope.

## Acceptance criteria

- [x] Use one concrete domain exception across HTTP, RPC and an MCP tool to compare current representations.
- [x] Specify opt-in mapping, native MCP error/result shape and compatibility with mask_error_details.
- [x] Separate domain failures, input validation, admission/auth errors and unexpected internal errors.
- [x] Do not leak internal exception details or import a sibling transport/concrete error registry into MCP.
- [x] Describe schema/tooling and source-checker implications without claiming unsupported MCP analysis.
- [x] Produce a transport-aware recommendation and implementation slices, or retain explicit native behavior with clear documentation.

## Verification

Read the MCP runtime/schema behavior and run a real mounted tool in the existing integration harness for baseline evidence.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Decision

**Decision: keep FastMCP's native error behavior for now and document it. Do not
add an automatic Canon problem binding to MCP until a concrete consumer needs one.**

Baseline with one exception, `ProjectNotFound`, declared once as
`Error(ProjectNotFound, status=404, code="project_not_found", ...)` and raised by
an HTTP route (`raises=[not_found]`) and by an MCP tool in the same composition:

| Surface | Result |
| --- | --- |
| HTTP route | 404 with a Problem body: `type`, `title`, `status`, `code` |
| MCP tool, no masking | `isError` result, text `Error calling tool 'get_project': <exception message>` |
| MCP tool, `McpOptions(mask_error_details=True)` only | Same text: the internal message is still returned |
| MCP tool on a server with `mask_error_details=True` | `isError` result, text `Error calling tool 'get_project'` (no detail) |
| MCP tool raising `ToolError("Project p1 not found")` on a masked server | `isError` result with exactly that safe text |

So MCP already has a native failure shape (`isError` plus text) and a native way to
send a deliberate message (`ToolError`). The finding worth acting on is the masking
scope: the root option does not mask child-server errors, which the guide only
implied. It now states this explicitly together with the `ToolError` pattern.

Design for a later opt-in, if a consumer needs known errors in MCP (not
implemented): an opt-in `raises=` on MCP tools that maps declared `Error`
instances to a `ToolError` containing only the stable `code` and `title` (never the
exception message or traceback), through the read-only `ErrorCatalog` protocol so
MCP imports neither a sibling transport nor the concrete registry. Input
validation, auth/admission failures and unexpected internal errors keep FastMCP's
native handling and masking; handshake `rejects` are unrelated. The source checker
would not analyze MCP tools until that exists, and no MCP client or schema
generation is implied. Implementation would be additive and needs its own ticket
with real-harness tests.

## Completion

- Implementation or decision PR: PR_LINK
- Final commit and verification: documentation only. `tests/integration/integrations/mcp/test_runtime.py`
(33 passed) covers failing tools as native `isError` results with rollback. The
table above came from a throwaway script that mounted one tool and one HTTP route
in a single `Composition` and called both through the real ASGI transport; no
runtime or public API changed.
- Remaining follow-ups: optional, not created: opt-in `raises=` for MCP tools as sketched above, only when a concrete consumer asks for it.
