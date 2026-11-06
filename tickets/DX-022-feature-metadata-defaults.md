# DX-022: Decide ownership of feature names, tags and transport namespaces

- Status: Done
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

- [x] Compare explicit declarations with derived defaults for a router reused across features/apps and nested RPC channels.
- [x] Specify precedence for explicit tags, namespace, operation_id and custom ID generators.
- [x] Identify exactly which existing OpenAPI/OpenRPC names and generated members would change.
- [x] Keep independent/reused transports configurable and detect ambiguous ownership.
- [x] Record either a narrowly scoped proposal with migration or a justified decision to keep current semantics.

## Verification

Use real exported contracts and generated naming examples as decision evidence; do not change public identifiers in this ticket.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Decision

**Decision: keep current semantics.** `Feature.name` does not derive namespaces,
operation IDs or tags, and no default is added.

Reasons, from the documented rules in [operation naming](../docs/routers.md):

- **Public names would change silently.** Derived defaults would alter every
  automatic ID of routers that currently have `namespace=None` (`get_project`
  becoming `<feature>.get_project`), and with them OpenAPI/OpenRPC operation
  names and the SDK members generated from them (`client.get_project` to
  `client.<feature>.get_project`). That is a generated-client incompatibility and
  therefore a breaking change under the release policy, even though the wire
  format is unchanged.
- **Ownership is ambiguous for reused routers.** The same router can be
  installed in different features or apps. A name derived from the installing
  feature would make the router's public API depend on where it is mounted;
  today it depends only on the router itself. Nested RPC channels have the same
  problem, and `include_channel(..., namespace=...)` already gives the explicit
  override.
- **Precedence would grow a third source.** Today an explicit `operation_id`
  wins, then the router namespace, then the bare function name, and a custom
  `generate_unique_id_function` replaces the rule. A feature-derived level would
  need a documented place in that order and an opt-out for each transport.
- **Collisions are already caught.** Duplicate operation IDs, including reused
  endpoints, fail composition preflight, so the explicit model does not hide
  ambiguity.

Hardening that does not change defaults, if wanted later: a non-failing
`Composition.check` hint when a router with `namespace=None` contributes
operations. It would be additive and needs its own ticket.

The routers guide now states that `Feature.name` is not a public API name and
where to declare namespaces and tags.

## Completion

- Implementation or decision PR: PR_LINK
- Final commit and verification: documentation only. The decision relies on the implemented rules in
`docs/routers.md` and the existing duplicate-ID preflight; no exported contract,
public identifier or default changed, so no drift or generator checks were needed.
- Remaining follow-ups: optional: a `check()` hint for routers without a namespace (not created; no demand shown).
