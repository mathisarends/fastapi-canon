# DX-013: Provide one explain command for documented diagnostic codes

- Status: Open
- Priority: P3
- Kind: Feature
- Baseline: `a366702` (2026-11-05)
- Depends on: [DX-012](DX-012-cli-help.md)
- Earlier review: C4.

## Problem and reproduction

The source checker emits CAN001-CAN003; composition emits canon.* codes.
`uv run --frozen fastapi-canon explain CAN003` is not a supported command.
The code families are documented separately in
[error checking](../docs/error-checking.md) and
[system checks](../docs/system-checks.md).

## Scope

Add a small diagnostic explanation entry point for existing codes. Do not
renumber public codes simply to make the two families look alike.

## Acceptance criteria

- [ ] Both code families resolve to explanations with meaning, a minimal example, a repair and relevant guide links.
- [ ] Unknown codes fail with an actionable message and stable nonzero exit status.
- [ ] Help/explanation does not import an application or optional runtime.
- [ ] Document whether machine-readable explanation output is supported; keep codes emitted by current checks compatible.
- [ ] Keep diagnostic data owned by the existing layer boundaries; do not make static analysis import composition runtimes.

## Verification

Exercise the public command for CAN001, CAN003, canon.routing.E002 and an unknown code; run optional-import and CLI checks.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
