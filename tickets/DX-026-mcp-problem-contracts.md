# DX-026: Decide MCP binding of shared application problems

- Status: Open
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

- [ ] Use one concrete domain exception across HTTP, RPC and an MCP tool to compare current representations.
- [ ] Specify opt-in mapping, native MCP error/result shape and compatibility with mask_error_details.
- [ ] Separate domain failures, input validation, admission/auth errors and unexpected internal errors.
- [ ] Do not leak internal exception details or import a sibling transport/concrete error registry into MCP.
- [ ] Describe schema/tooling and source-checker implications without claiming unsupported MCP analysis.
- [ ] Produce a transport-aware recommendation and implementation slices, or retain explicit native behavior with clear documentation.

## Verification

Read the MCP runtime/schema behavior and run a real mounted tool in the existing integration harness for baseline evidence.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
