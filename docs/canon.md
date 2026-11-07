# Canon package and public API

Canon includes
[shared problem contracts](problems.md).
RPC problem envelopes, terminal SSE control events, and the combined client
facade are included in this source.

This document records the implemented package boundaries and public entry points
in this checkout. Published and prepared versions are tracked in
[releases](releases.md). The
[codegen guide](unified-canon-concept.md) describes the implemented common
configuration, domain facade and explicit support limits.

The canonical repository for development and future releases is
[`mathisarends/fastapi_canon`](https://github.com/mathisarends/fastapi_canon).
The [repository migration](repository-migration.md) preserves the imported Git
history and records the publisher cutover. Repository names do not change the
`fastapi-canon` distribution or its `fastapi_canon` imports.

## Package and dependencies

`fastapi-canon` is the only active library distribution. Its wheel contains
composition, HTTP contracts, SSE, WebSockets, WebRTC, MCP, telemetry declarations,
and the existing generators. They share one Canon version.

Python extras select third-party dependencies, not source modules or additional
Canon distributions. The base depends on FastAPI, Pydantic and Dishka. Importing
`fastapi_canon` and displaying root CLI help must work without optional generator
dependencies, telemetry runtimes or media engines.

| Extra | Purpose |
| --- | --- |
| `sse` | FastAPI and Pydantic versions required by typed SSE |
| `websockets` | Pydantic, core-schema and typing requirements for RPC and sockets |
| `webrtc` | AnyIO, Starlette and typing requirements for session signaling |
| `aiortc` | Optional reference media engine; use with `webrtc` |
| `otel` | Native FastAPI telemetry, observed Dishka ports and WebSocket middleware |
| `otel-httpx`, `otel-sqlalchemy` | Explicit per-client HTTPX and per-engine SQLAlchemy telemetry adapters |
| `codegen` | Template, schema-validation and model dependencies for generators |
| `database` | Async SQLAlchemy engine and session ownership, without a driver |
| `sqlite`, `postgres` | Database integration with aiosqlite or asyncpg |
| `sqlmodel` | SQLModel sessions on the same database integration |
| `migrations` | Alembic revision generation, upgrades and schema checks |

The root `pyproject.toml` builds `fastapi_canon/` directly. Tests, examples, documentation
and development tooling are organized by purpose, without retained package
projects. See [repository architecture](architecture.md) for the folder map.
The release configuration selects only the root Canon library.

Dependency ranges live in the root `pyproject.toml`;
[compatibility](compatibility.md) records the tested scope. The generated SDK's
HTTP or WebSocket client dependencies belong to its consumer. Application
providers and exporters remain application-owned.

## Public namespaces

Core composition and HTTP contracts live in `fastapi_canon`. All optional
integrations live under `fastapi_canon.integrations`: SSE, WebSockets, WebRTC
and telemetry. A namespace organizes code inside the same wheel; it does not
represent another PyPI distribution.

| Responsibility | Public imports | Guide |
| --- | --- | --- |
| Shared application router and composable channels | `fastapi_canon.APIRouter`, `fastapi_canon.integrations.websockets.RpcChannel` | [One feature router](routers.md) |
| Application composition and Dishka lifecycle | `fastapi_canon.Composition`, `Feature` | [Composition](composition.md#feature-composition) |
| Typed transport dependencies and context hooks | `fastapi_canon.Inject` | [Dependency injection](integrations/websockets/dependencies.md) |
| Composition checks and installed HTTP descriptions | `Composition.check(app)`, `fastapi_canon.inspect_app(app)`; values `fastapi_canon.diagnostics.CheckReport`, `CheckIssue`, `CheckContext`, `CompositionInfo`, `FeatureInfo`, `OperationInfo` | [System checks](system-checks.md) |
| Database ownership, migrations and locks | `fastapi_canon.integrations.database.Database`, `AlembicMigrations`, `TransactionLocks` | [Databases](database.md) |
| Short service scopes | `fastapi_canon.Scoped`, `ServiceScope` | [Databases](database.md) |
| HTTP routes and contracts | `fastapi_canon.APIRouter`, `RouterPolicy`, `Error`, `ErrorOptions`, `CanonResponse`; `Problem`; `fastapi_canon.error.ErrorRegistry` for manual assembly without `Composition` | [HTTP contracts](composition.md#error-contracts) |
| Typed SSE | `APIRouter.sse()` | [SSE contracts](streams.md) |
| WebSocket JSON-RPC | `APIRouter.include_channel()` with `fastapi_canon.integrations.websockets.RpcChannel` | [WebSockets](integrations/websockets/README.md) |
| Typed or raw WebSocket messages and binary streams | `APIRouter.include_socket()`, `APIRouter.include_stream()` with `@channel.socket`/`@channel.stream`; `RpcSocket` | [Sockets](integrations/websockets/sockets.md), [binary streams](integrations/websockets/streams.md) |
| WebRTC with Canon admission errors | `APIRouter.webrtc()`, `fastapi_canon.integrations.webrtc.Session`, `SessionCapacity`; engine adapters in `.webrtc.engines` | [WebRTC admission](webrtc.md) |
| Contract export | `fastapi_canon.export_contracts`, `fastapi_canon.contracts.Contracts`, `fastapi-canon contracts export` | [Application export](contracts.md) |
| OpenAPI contracts without `Composition` | `fastapi_canon.openapi.install_openapi_contracts`, `validate_openapi_contracts` | [Composition](composition.md) |
| Startup configuration errors | `fastapi_canon.exceptions.ConfigurationError` and its subclasses | [Problems](problems.md) |
| Telemetry declarations | `fastapi_canon.integrations.opentelemetry.Telemetry`, `instrument` | [Telemetry](telemetry.md) |
| SQLAlchemy engine instrumentation declaration | `fastapi_canon.integrations.opentelemetry.sqlalchemy.SqlAlchemyInstrumentation` | [Engine lifetime](database.md#engine-instrumentation-lifetime) |

Only the modules in this table are public: the `fastapi_canon` root,
`fastapi_canon.error`, `fastapi_canon.diagnostics`, `fastapi_canon.exceptions`,
`fastapi_canon.openapi`, `fastapi_canon.contracts` and the
`fastapi_canon.integrations.*` facades with their documented submodules. Other
modules are internal even without a leading underscore and may change in any
release. The root exports only names needed to build an application; a type
used by a transport method, such as `RpcChannel` or `Session`, is imported from
that transport's namespace.

Typical application imports are explicit:

```python
from fastapi_canon import APIRouter, Composition, Feature
from fastapi_canon.integrations.websockets import RpcChannel
from fastapi_canon.integrations.opentelemetry import Telemetry, instrument
from fastapi_canon.integrations.webrtc import Session
```

Core imports do not eagerly load any transport. `APIRouter.sse()`,
`include_channel()` and `webrtc()` import their integration on first use; import
the aiortc adapter only when choosing that engine. Raw sockets and binary
WebSocket streams are not JSON-RPC channels and keep their own
`CanonWebSocketRouter` declarations.

WebSocket class names retain `Rpc` when they describe RPC or its declared socket
contracts. The namespace names the transport; it does not make every WebSocket
message an RPC method. Protocol-specific methods and lifecycles stay explicit.

## Composition and runtime ownership

Applications use feature slices and inward dependencies. Domain code is framework-free.
`Composition` installs features and owns the application Dishka container.
Feature routers contribute routes and declarations; dependencies own
authentication, authorization and application resources.

`Composition(features=features, request_logging=True)` enables structured HTTP completion and
abort records independently of telemetry. It defaults to disabled and requires
no extra package. See [request logging](request-logging.md) for the record fields,
trace correlation and application-owned logging setup.

SSE preconditions and WebRTC admission can use Canon HTTP problems before a
response or connection is accepted. Once streaming or protocol communication
starts, failures follow that transport's declared protocol. JSON-RPC envelopes,
raw/binary sockets and `fastapi-webrtc.v1` retain their existing wire behavior.

Telemetry is explicit at the composition boundary. Pass the tuple returned by
`instrument(Port, OtherPort, ...)` directly to `Feature.instrumentation`, including
for async `__call__` use cases. The call's `expected` and `component` options apply
to every port in that group. Composition
creates its Dishka decorators only when telemetry is enabled, leaving providers
usable independently. `Telemetry.websocket_middleware()` explicitly bridges the
selected providers into a WebSocket service. Optional features do not create
exporters or own application SDK providers.

## Commands and generated artifacts

There is one installed command, `fastapi-canon`, also available as
`python -m fastapi_canon`. The implemented command families are:

| Command | Responsibility |
| --- | --- |
| `fastapi-canon contracts export` | Export the composed app's HTTP/SSE, RPC and WebRTC contracts without starting runtime resources |
| `fastapi-canon generate` | Plan contracts together and generate one domain SDK with shared models/errors |
| `fastapi-canon sse` | Export OpenAPI/SSE contracts and generate Python/TypeScript clients |
| `fastapi-canon websockets` | Export OpenRPC/socket contracts and generate Python/TypeScript clients |
| `fastapi-canon webrtc` | Export session contracts and generate TypeScript browser clients |
| `fastapi-canon check errors` | Check HTTP, SSE, RPC and WebRTC error declarations without importing application code |
| `fastapi-canon skill install` | Install the bundled backend-building skill and resources into `.agents` and `.claude`; `--force` refreshes both, `--destination` selects one directory |

### Choosing a generation command

Start with `fastapi-canon generate`. Use the others only for the cases below.

| Need | Command | Notes |
| --- | --- | --- |
| One SDK for a service (HTTP, SSE, RPC, WebRTC) in Python and TypeScript | `fastapi-canon generate` with `[codegen]` in `fastapi-canon.toml` | Preferred. Plans all sources together; `--check`/`--diff` detect drift, `--offline` reads saved contracts |
| Contract files only (OpenAPI, OpenRPC, WebRTC) for review or other tools | `fastapi-canon contracts export --app module:app --output-dir DIR` | `--kind` selects one contract; `--check`/`--diff` detect drift; starts no lifespans |
| SSE client with accepted-diagnostics control or a stored `[tool.fastapi-canon.sse]` table | `fastapi-canon sse schema/generate/check/sync` | `--strict`, `--allow-diagnostics`, `--diagnostics` and `sync` have no common equivalent |
| WebSocket client from an `RpcChannel` or `RpcContract` without a FastAPI app, or with socket-protocol bindings | `fastapi-canon websockets schema/generate` | `--binding`, `--transport-module`, `--api-root`, `--api-name`, `--server` and a multi-client `--config` manifest |
| Browser client for WebRTC sessions only | `fastapi-canon webrtc contract/generate` | TypeScript only; `--client-name`, saved contract via `--contract` |

Each specialized family keeps its own configuration and ownership manifest, so a
directory is owned by one workflow: do not generate the same output with `generate`
and a specialized command. Nothing is deprecated; the specialized commands remain
supported for the capabilities above. Common generation covers JSON HTTP, SSE, RPC,
raw-socket and WebRTC bindings with the [support limits](unified-canon-concept.md#connections-and-supported-scope).

The specialized emitters retain their configuration, runtime options and ownership
manifests. The preferred `fastapi-canon generate` workflow plans sources together and
owns one combined SDK output per target. It includes a JSON HTTP binding as well
as internal SSE, JSON-RPC, socket and TypeScript WebRTC bindings. Generated files
are changed through generators/templates and checked for drift.

## Documentation and release ownership

| Document | Authority |
| --- | --- |
| This document | Package boundaries, public namespaces and runtime ownership |
| Package and feature guides | Implemented signatures, options and runnable examples |
| [Migration guide](package-migration.md) | Installation and import changes for existing consumers |
| [Codegen guide](unified-canon-concept.md) | Implemented common generation workflow and support limits |
| [Contributing](contributing.md) | Development commands and quality gates |
| [Compatibility](compatibility.md) | Tested dependencies and consumer limits |
| [Releases](releases.md) | Prepared versions, publication gates and historical evidence |

Transport guides live in `docs/integrations/`, examples in `examples/` and suites
in the shared test roots. Historical changelogs remain available in Git history;
the root `LICENSE` retains the imported code's MIT notice. Releases use one Canon
changelog, version, tag and artifact set.

## MCP composition

`fastapi_canon.integrations.mcp` exports `McpServer`, `Inject`, `McpMount`, `McpScope`, and
`McpOptions`. Install `fastapi-canon[mcp]` for the FastMCP runtime. Features
contribute explicit namespaced mounts; `Composition(mcp=McpOptions())` owns the
common HTTP endpoint and lifespan. See the [MCP guide](integrations/mcp/README.md).
