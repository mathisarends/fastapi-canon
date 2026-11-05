# DX-028: Document minimum dependencies in one current overview

- Status: Open
- Priority: P3
- Kind: Documentation
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: S3.

## Problem and reproduction

Compare the base, sse, otel, webrtc and mcp requirements in
[pyproject.toml](../pyproject.toml) with
[compatibility evidence](../docs/compatibility.md). Minimum versions differ for
real integration requirements. Their rationale and tested scope are distributed
across several sections rather than a compact current table.

## Scope

Add a current dependency/extra overview to the existing authoritative guide.
Keep historical tested-release evidence and the root lockfiles intact.

## Acceptance criteria

- [ ] Show declared minimum Python/framework/model requirements per relevant extra, with a short reason for differences.
- [ ] Distinguish declared constraints, tested minimum/locked combinations and published versus prepared source state.
- [ ] Do not raise core requirements merely to make table rows identical.
- [ ] Link existing installed-wheel minimum checks and explain that future dependency combinations are not automatically tested.
- [ ] Validate every stated constraint against pyproject.toml and existing release evidence.

## Verification

Manually compare the table with the actual requirement entries and compatibility records. Run documentation/style gates; dependency changes are outside scope.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
