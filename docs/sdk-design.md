# SDK design: shared contracts, separate clients

Status: accepted for the prepared 0.17.0 source; not a publication record.

## Problem

The domain SDK attempted to merge HTTP, SSE, JSON-RPC, raw sockets and WebRTC
into one public client. It added an HTTP generator, global model identities,
cross-source naming rules, binding rewrites, common connection ownership and
another set of generated options/error classes. Consumers still needed their
HTTP framework tooling and application-specific connection policy.

In Maximus Trading, the HTTP generator supplies TanStack Query keys/options and
mutation helpers. Streams own completion and reader cancellation. WebSocket
terminal/browser sessions require different reconnect and replay policies from
ordinary shared RPC calls. A common facade could obscure these differences but
could not remove their implementation or testing cost.

## Decision

Keep one generation/check workflow and separate client APIs. Use established
OpenAPI tooling for HTTP. Canon owns its specialized SSE/WebSocket/WebRTC
generators, public error bindings and contract export. A small external command
target integrates existing HTTP scripts without a generator plugin framework.

Reuse is anchored in Python models and `Error` declarations. Each exported
contract describes its transport's public representation. Separate generated
types are acceptable because they are derived artifacts, not independently
maintained specifications. Generator and contract tests protect their meaning.

Preserve the shared public problem contract and the small problem templates
already reused by Canon's generators. Do not require one exception-class identity
across independently generated clients. HTTP status and transport envelopes
remain protocol-specific; stable `type` URIs identify application problems.

## Removed complexity

- Python/TypeScript HTTP client emission, multipart client values and HTTP runtime.
- Cross-source model registry, semantic identity/name mappings and reference rewriting.
- Combined operation graph, global operation naming and merged facade hierarchy.
- Binding postprocessing, facade-specific call results and common lifetime runtime.
- The unified SDK manifest and SDK-specific tests, smoke paths and documentation.

The CLI/configuration are replaced, rather than retaining a second generation
mode. Version 2 exports contracts once and delegates generation/checking. Native
generators retain their existing ownership and stale-file cleanup mechanisms.

## Preserved behavior

Shared server errors, Pydantic wire policy, HTTP/OpenAPI response metadata,
native uploads, stable operation IDs, composition, DI and static error checking
remain useful independently. Stream completion, cancellation and managed resource
cleanup are transport guarantees and remain tested. The library stays one root
package with one version; SDK simplification does not split the distribution.

## Tradeoffs and verification

Consumers import several client APIs and may have duplicate generated types.
Application adapters own auth refresh, optional error normalization and connection
policy. Canon avoids maintaining a competing HTTP/framework stack and a global
type-merging system. External check commands must honor the read-only/exit-code
contract; Canon cannot sandbox third-party generators or roll back their writes.

Live checks compare contracts before invoking client checks. Stale contracts block
dependent checks instead of reporting clients current against old inputs. Native
generation preflights before writing; selected target results and failures are
reported together. Offline checking verifies saved inputs only.

Regression coverage retains shared-error behavior across real HTTP/SSE/RPC routes,
specialized client execution and lifecycle checks, installed-wheel smoke checks,
and read-only drift checks including stale files and target selection. See
[the developer-facing workflow](codegen.md) for configuration and migration.
