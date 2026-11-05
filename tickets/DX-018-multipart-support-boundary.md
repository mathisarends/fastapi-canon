# DX-018: Decide and slice common SDK multipart support

- Status: Open
- Priority: P3
- Kind: Decision
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: G5.

## Problem and reproduction

The [common generation support boundary](../docs/unified-canon-concept.md)
explicitly excludes multipart/files. Define a minimal FastAPI upload route
with UploadFile/File and inspect its exported OpenAPI; attempt generation for
each existing common target and record the preflight diagnostics.
This is a product capability gap, not a demonstrated parser regression.

## Scope

Produce a concrete support decision and implementation slices for the existing
common codegen pipeline. Do not add partial multipart support across every
emitter in one unreviewable change.

## Acceptance criteria

- [ ] Document the first supported case, including file inputs, scalar form fields, repeated parts and excluded nested encodings.
- [ ] Define Python and TypeScript public argument types, streaming/ownership behavior and cancellation expectations.
- [ ] Identify IR/planner, Python-emitter, TypeScript-emitter and real-consumer slices with explicit dependencies.
- [ ] Record current fail-before-write behavior and the tests that must preserve it for unsupported encodings.
- [ ] State SDK/version compatibility impacts and a recommendation backed by a concrete consumer use case.
- [ ] Create follow-up implementation tickets only for the selected support boundary; a justified deferral is an acceptable decision outcome.

## Verification

Export a real minimal app contract and exercise both current targets; link the resulting evidence and proposed acceptance cases.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
