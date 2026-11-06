# DX-023: Evaluate public facade organization without arbitrary export cuts

- Status: Done
- Priority: P3
- Kind: Decision
- Baseline: `a366702` (2026-11-05); inventory refreshed at `df05e84` (2026-11-06)
- Depends on: None
- Earlier review: S1/S2; defer implementation until its benefit is established.

## Problem and reproduction

Inspect [root exports](../fastapi_canon/__init__.py) and
[WebSocket exports](../fastapi_canon/integrations/websockets/__init__.py).
Both include everyday APIs and advanced protocol/diagnostic types.
The diagnostics module already exists; its mere existence does not remove root
re-exports. Export count alone is not evidence of a defect.

## Scope

Inventory real consumer imports and document an organization proposal.
Do not move public types, create a new workspace or remove exports in this ticket.

## Acceptance criteria

- [x] Classify actual exports by user task and identify concrete autocomplete/discoverability problems.
- [x] Reuse existing public namespaces when they fit; verify any proposed new namespace against architecture rules.
- [x] Include documented imports, examples, bundled skill and generated-consumer impacts.
- [x] Specify a migration consistent with root and integration AGENTS.md; do not assume deprecation shims are permitted everywhere.
- [x] Preserve lazy optional imports and one Canon version.
- [x] Conclude with a recommendation; declining an export reduction is a valid outcome.

## Verification

Verify inventory against the actual facades and search consumer imports with rg. Link the proposed documentation/import examples.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Inventory

Consumer imports were counted per `__all__` name in tracked `.py` and `.md` files
outside `tests/`, `tickets/`, `docs/history/`, `CHANGELOG.md` and library source.
The bundled skill under `fastapi_canon/skills/` counts as a consumer. Only
`from <facade> import ...` statements were counted. The count cannot see external
users; it identifies which names the documented workflows actually require.

| Facade | Exports | Never imported by consumers | Most imported |
| --- | --- | --- | --- |
| `fastapi_canon` | 43 | 19 | `APIRouter` 44, `Composition` 42, `Feature` 41, `Error` 16, `ErrorOptions` 14, `Inject` 12, `RpcChannel` 12 |
| `fastapi_canon.integrations.websockets` | 75 | 48 | `RpcChannel` 16, `CanonWebSocketRouter` 15, `RpcModel` 13, `Inject` 11, `RpcRejection` 7 |
| `fastapi_canon.integrations.webrtc` | 40 | 25 | `Session` 8, `DataChannelSpec` 4, `Direction`, `MediaPolicy`, `Peer`, `WebRtcRouter` 3 each |
| `fastapi_canon.integrations.sse` | 3 | 1 | `StreamRouter` 4 (examples only), `CanonStreamRouter` 1 |

Concrete discoverability problems:

- **Two ways to declare a transport.** [Routers](../docs/routers.md) and
  [canon.md](../docs/canon.md) document `APIRouter.sse()`, `include_channel()` and
  `webrtc()` as the application path, while the facades still export
  `StreamRouter`, `CanonStreamRouter`, `WebRtcRouter` and `CanonWebRtcRouter`, and
  `examples/sse/agent_runs` and `examples/webrtc/*` use them. `APIRouter.webrtc()`
  itself constructs a `CanonWebRtcRouter` per declaration
  ([api_router.py](../fastapi_canon/api_router.py)), so the class is an adapter, not
  an application API.
- **Two router classes in the root.** `CanonRouter` is the base of `APIRouter`.
- **Wire and runtime internals beside application types.** The WebSocket facade
  exports envelope types (`RpcNotification`, `RpcRequestId`, `RpcResponseMessage`,
  `RpcSocketMessage`), endpoint/resolver internals and `RpcCodec`/`RpcProtocol`
  next to `RpcChannel`. Autocomplete on the facade lists 75 names.
- **Duplicate paths.** `RpcChannel` is importable from the root and from the
  WebSocket facade (12 and 16 consumer imports). `__version__` is re-exported by
  every integration facade although there is one Canon version.
- **Standard-library re-export.** `fastapi_canon.HTTPMethod` is `http.HTTPMethod`.

Generated clients are not affected. Committed SDKs import their own runtimes, and
the SSE generator rewrites its template's `fastapi_canon.codegen.templates.problem`
import to a local `.problem` module
([sse/codegen/python.py](../fastapi_canon/integrations/sse/codegen/python.py)).
The bundled skill only imports root core names and `RpcChannel` from the root
(`references/transports-and-sdk.md`).

## Decision

Reduce the facades in one breaking release. Do not keep aliases.

### Rule for a public name

A **facade** name is one an application author must **write** in the common
workflow: to declare, configure, pass, catch, subclass or implement something.
Wire envelopes and runtime building blocks are not facade names.

Every type that appears in a public signature keeps a public import path, so
users can annotate what they pass and receive. That path may be a public
submodule instead of the facade. This preserves the existing policy tested by
`test_public_signature_types_are_exported`
([test_public_api.py](../tests/unit/integrations/websockets/test_public_api.py)),
which covers `RpcResolverLike`, `RpcResponseMessage`, `RpcProtocol`,
`RpcErrorBinding` and `RpcErrorDeclaration`. A name with zero consumer imports
therefore becomes internal only when no public signature uses it; the
implementation tickets verify this per name.

Transport methods belong to `APIRouter` in the root and load their integration
lazily on first use. The types used in those declarations (`RpcChannel`,
`Session`, ...) live in their transport namespace. Specialized transport routers
are internal adapters, except `CanonWebSocketRouter`: raw sockets and binary
streams are not JSON-RPC channels and have no `APIRouter` method.

### Public namespaces

Only the facades and the submodules listed in `docs/canon.md` are public. Every
other module is internal even without a leading underscore. Tests may import
internal modules by their module path; they still must not access private members.

| Namespace | Contents after the change |
| --- | --- |
| `fastapi_canon` | Application composition, injection, errors, responses, contract export |
| `fastapi_canon.error` | `ErrorRegistry`, `Problem`, `ErrorConfigurationError` and the existing error API |
| `fastapi_canon.diagnostics` | Check and inspection result types |
| `fastapi_canon.exceptions` | Startup configuration errors |
| `fastapi_canon.openapi` | OpenAPI contract installation for applications without `Composition` |
| `fastapi_canon.contracts` | `Contracts` and the existing contract API |
| `fastapi_canon.integrations.database` | Database, migrations and transaction locks |
| `fastapi_canon.integrations.websockets` (+ `.middleware`, `.adapters`, `.errors`) | Channels, sockets, connection admission; middleware authoring; framework adapters; protocol errors |
| `fastapi_canon.integrations.webrtc` (+ `.engines`) | Session declaration and peer callbacks; engine authoring and engine adapters |
| `fastapi_canon.integrations.sse` | No public names remain beyond the facade module itself |
| `fastapi_canon.integrations.mcp`, `.opentelemetry` | Unchanged (MCP has 5 names) |

All target modules already exist. No new namespace is introduced. Two package
facades gain re-exports: `websockets/adapters/__init__.py` (currently empty) and
`webrtc/engines/__init__.py`. The internal `webrtc/engine.py` module keeps the
definitions; one public engine path avoids `engine` beside `engines`. Neither
facade may import the Dishka, OpenTelemetry or aiortc adapters eagerly.

### Root `fastapi_canon`

Keep: `APIRouter`, `Feature`, `Composition`, `RouterPolicy`, `Inject`,
`ServiceScope`, `Scoped`, `Error`, `ErrorOptions`, `ExceptionHandlerSpec`,
`CanonResponse`, `ResponseHeaders`, `export_contracts`, `__version__`.

| Removed from the root | New import |
| --- | --- |
| `HTTPMethod` | `from http import HTTPMethod` |
| `CanonRouter` | Internal; use `APIRouter` |
| `CheckContext`, `CheckIssue`, `CheckReport`, `CheckResult`, `CompositionInfo`, `DependencyInfo`, `ErrorInfo`, `FeatureCheck`, `FeatureInfo`, `OperationInfo` | `fastapi_canon.diagnostics` |
| `Database`, `AlembicMigrations`, `TransactionLocks`, `run_alembic_environment` | `fastapi_canon.integrations.database` |
| `RpcChannel` | `fastapi_canon.integrations.websockets` |
| `ErrorRegistry`, `Problem`, `ErrorConfigurationError` | `fastapi_canon.error` |
| `ConfigurationError`, `FeatureConfigurationError`, `ResponseConfigurationError` | `fastapi_canon.exceptions` (the latter two are defined in `feature.py` and `response.py` today) |
| `install_openapi_contracts`, `validate_openapi_contracts` | `fastapi_canon.openapi` |
| `Contracts` | `fastapi_canon.contracts` |
| `ProviderFactory`, `ProviderSource`, `RouterFactory` | Internal; `Feature`, `Composition` and `Composition.override()` spell out `Provider \| Callable[[], Provider]` and `Callable[[], APIRouter]` so no internal alias remains in a public signature |

`RpcChannel` leaves the root for consistency with `Session`: both are declaration
types for a core `APIRouter` method. It is already loaded lazily
([public_exports.py](../fastapi_canon/public_exports.py)), so the change is about
one import path per name, not import cost. With `RpcChannel` and the database
names gone, `public_exports.py` and the root `__getattr__` can be removed.

### WebSocket facade

Keep: `RpcChannel`, `CanonWebSocketRouter`, `RpcModel`, `Inject`, `RpcConnection`,
`RpcHandshake`, `RpcReject`, `RpcRejection`, `RpcErrorBinding`, `RpcError`,
`RpcLimits`, `RpcDrain`, `RpcConnectionExpiry`, `RpcConnectionClose`,
`RpcDisconnect`, `RpcSocket`, `RpcSocketProtocol`, `RpcMessageType`,
`RpcBinaryInput`, `RpcBinaryOutput`, `ServerVariable`.

- `.middleware`: `RpcMiddleware`, `RpcMiddlewareLike`, the scope and outcome types,
  endpoint info types, `RpcRequestContext`, `RpcSuccess`, `RpcFailure`.
- `.adapters`: `FastApiSocket`, `serve_websocket`.
- `.errors`: JSON-RPC protocol errors (`RpcParseError`, `RpcMethodNotFoundError`,
  `RpcInvalidParamsError`, `RpcInvalidRequestError`, `RpcInternalError`,
  `RpcRequestCancelledError`, `RpcRequestTimeoutError`,
  `RpcServerShuttingDownError`), `RpcErrorCode`, `ProtocolDefinitionError`.
- Signature types without an everyday use move to the submodule of their concern
  instead of becoming internal: `RpcServer` and `RpcResponseMessage` (returned by
  `RpcServer.handle()`) to `.adapters`; `RpcProtocol` (returned by
  `RpcChannel.freeze()` and `.protocol`) to the submodule DX-031 selects, or the
  methods leave the public surface in the same change.
- Internal: envelope and message types not used in public signatures,
  endpoint/contract internals, `RpcCodec`, `LOGGER_NAME` (document the logger
  name instead) and `__version__`.
- Verify per name, then place in the facade or a submodule: names users pass as
  arguments, such as `call_scope`/`RpcResolverScope` for `resolver_scope=`,
  `RpcResolver`, `RpcResolverLike` (`resolver=`), `RpcErrorDeclaration`
  (`raises=`), `RpcErrorMapper`, `RpcRejectionMapper`, `RpcRejections`,
  `RpcBeforeAccept`, `RpcValidationIssue` and `error_message`.

### SSE and WebRTC facades

- `StreamRouter` and `CanonStreamRouter` become internal; `APIRouter.sse()` is the
  declaration API. `examples/sse/agent_runs` moves to it.
- `WebRtcRouter` and `CanonWebRtcRouter` become internal; `APIRouter.webrtc()` is
  the declaration API. The facade keeps declaration and callback types (`Session`,
  `MediaPolicy`, `Direction`, `Role`, `DataChannelSpec`, `SessionLimits`,
  `IceServer`, `IceServers`, `TurnRestCredentials`, `Peer`, `Channel`,
  `IncomingTrack`, `OutgoingTrack`, `EventHook`, `SessionEvent`, the WebRTC errors
  and protocol constants). Engine-authoring types (`Engine`, `EngineCapabilities`,
  state, stats and description types) move to `.engines`, next to the adapters.
- `WebRtcRouter(max_sessions=n)` limits all sessions of one router together.
  `APIRouter.webrtc()` creates one adapter per declaration and only supports
  per-session `max_sessions`. Instead of moving this option onto the transport-
  neutral `APIRouter`, add an explicit shared `SessionCapacity(max_sessions=n)`
  passed as `capacity=` to each declaration. It can be shared across routers and
  features, and its `active` count replaces `WebRtcRouter.active_sessions`.
  The existing slot mechanism already acquires several counters at once.

### Release and migration

- One minor release, without aliases. The WebSocket
  [AGENTS.md](../fastapi_canon/integrations/websockets/AGENTS.md) requires removing
  superseded Python APIs instead of keeping parallel ones; the root rules do not
  require shims either. `CHANGELOG.md` `Unreleased` already targets 0.16.0. If
  0.16.0 is published before all three implementation tickets land, the remaining
  ones target the following minor release together.
- One `### Breaking changes` table lists every old and new import path.
- Implementation is split by facade into [DX-030](DX-030-root-facade-reduction.md),
  [DX-031](DX-031-websocket-facade-reduction.md) and
  [DX-032](DX-032-transport-router-adapters.md). Each updates docs, examples, the
  bundled skill and `docs/canon.md` for its facade.

## Completion

- Implementation or decision PR: [#102](https://github.com/mathisarends/fastapi-canon/pull/102)
- Final commit and verification: Inventory script run against `df05e84`; facade
  modules, target submodules and the generator import rewrite verified in source.
- Remaining follow-ups: DX-030, DX-031, DX-032.
