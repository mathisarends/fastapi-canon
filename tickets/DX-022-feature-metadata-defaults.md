# DX-022: Decide ownership of feature names, tags and transport namespaces

- Status: Open
- Priority: P3
- Kind: Decision
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: Remaining R4.

## Problem and reproduction

HTTP/SSE namespace-derived IDs are already implemented by #76.
Feature.name, APIRouter.namespace/tags and RpcChannel namespaces are still
independent. Read [operation naming](../docs/routers.md): included routers retain
their own namespaces and Feature.name does not contribute to IDs.

## Scope

Evaluate whether defaults derived from Feature.name are desirable. Keep stable
public operation IDs and reusable-router ownership explicit; do not undo #76.

## Acceptance criteria

- [ ] Compare explicit declarations with derived defaults for a router reused across features/apps and nested RPC channels.
- [ ] Specify precedence for explicit tags, namespace, operation_id and custom ID generators.
- [ ] Identify exactly which existing OpenAPI/OpenRPC names and generated members would change.
- [ ] Keep independent/reused transports configurable and detect ambiguous ownership.
- [ ] Record either a narrowly scoped proposal with migration or a justified decision to keep current semantics.

## Verification

Use real exported contracts and generated naming examples as decision evidence; do not change public identifiers in this ticket.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
