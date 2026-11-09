# SDK design: shared contracts, separate clients

Accepted for the prepared 0.17.0 source. For usage and migration, see
[client generation](codegen.md).

## Decision

Keep one contract export and generation/check workflow, with separate client
APIs. Use external OpenAPI tooling for HTTP and Canon's specialized SSE,
WebSocket and WebRTC generators.

The combined SDK duplicated HTTP framework tooling and added model merging,
binding rewrites and shared connection management. Separate clients let HTTP
tools provide query and mutation helpers while stream and session clients retain
their own cancellation, reconnect and resource ownership rules.

Python models and `Error` declarations remain the shared source. Each exported
contract describes its public wire representation. The problem `type` URI
identifies an error across transports; generated exception classes have separate
runtime identities.

## Consequences

Consumers may have duplicate generated types and import several clients.
Application adapters own shared authentication, error normalization and connection
policy. Native generators retain their ownership manifests. External tools own
their outputs and must supply a read-only check command.

The workflow checks live contracts before checking clients and preflights native
targets before writing. Offline checks verify saved inputs only. Generation
cannot roll back partial writes by external tools.
