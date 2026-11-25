# 21. Public API

Canon is one `fastapi-canon` distribution and one `fastapi_canon` import package.
Integrations share its version; extras select optional dependencies.
The base requires FastAPI, Pydantic and Dishka. Core imports and CLI help work
without codegen dependencies, telemetry runtimes or media engines.

These docs follow the checkout. See [releases](93-releases.md#current-versions) for
published and prepared versions and [compatibility](25-compatibility.md) for tested
scope.

## Package and dependencies

| Extra | Purpose |
| --- | --- |
| `uploads` | Multipart parser for single/multiple HTTP file uploads and form fields |
| `sse` | FastAPI and Pydantic versions required by typed SSE |
| `websockets` | Pydantic, core-schema and typing requirements for RPC and sockets |
| `webrtc` | AnyIO, Starlette and typing requirements for session signaling |
| `aiortc` | Optional reference media engine; use with `webrtc` |
| `otel` | Native FastAPI telemetry, observed Dishka ports and WebSocket middleware |
| `otel-httpx`, `otel-sqlalchemy` | Automatic Dishka resource telemetry and explicit per-client/per-engine adapters |
| `codegen` | Template, schema-validation and model dependencies for generators |
| `database` | Async SQLAlchemy engine and session ownership, without a driver |
| `sqlite`, `postgres` | Database integration with aiosqlite or asyncpg |
| `sqlmodel` | SQLModel sessions on the same database integration |
| `migrations` | Alembic revision generation, upgrades and schema checks |

## Public namespaces

| Responsibility | Public imports | Guide |
| --- | --- | --- |
| Shared application router and composable channels | `fastapi_canon.APIRouter`, `fastapi_canon.integrations.websockets.RpcChannel` | [One feature router](20-routers.md) |
| Application composition and Dishka lifecycle | `fastapi_canon.Composition`, `Feature` | [Composition](05-composition.md#features) |
| Typed transport dependencies and context hooks | `fastapi_canon.Inject` | [Dependency injection](integrations/websockets/06-dependencies.md) |
| Composition checks and installed HTTP descriptions | `Composition.check(app)`, `fastapi_canon.check_app(app)`, `fastapi_canon.inspect_app(app)`; values `fastapi_canon.diagnostics.CheckReport`, `CheckIssue`, `CheckContext`, `CompositionInfo`, `FeatureInfo`, `OperationInfo` | [System checks](23-checks-and-diagnostics.md) |
| Database ownership, feature tables, migrations and locks | `fastapi_canon.integrations.database.Database`, `Tables`, `AlembicMigrations`, `AcquireTransactionLock` | [Databases](09-database.md) |
| Short service scopes | `fastapi_canon.Scoped`, `ServiceScope` | [Databases](09-database.md) |
| HTTP routes and contracts | `fastapi_canon.APIRouter`, `RouterPolicy`, `Error`, `ErrorOptions`, `CanonResponse`; `Problem`; `fastapi_canon.error.ErrorRegistry` for manual assembly without `Composition` | [HTTP contracts](02-errors.md) |
| Streaming HTTP request bodies | `fastapi_canon.BinaryBody` (0.28.0) | [File uploads](08-file-transfers.md#downloads) |
| Typed SSE | `APIRouter.get(..., response_class=EventSourceResponse)` | [SSE contracts](03-streaming.md) |
| WebSocket JSON-RPC | `APIRouter.include_channel()` with `fastapi_canon.integrations.websockets.RpcChannel`; `Annotated[T, Path()]` for path variables | [WebSockets](04-websockets.md) |
| Typed or raw WebSocket messages and binary streams | `@APIRouter.socket()`, `@APIRouter.binary_stream()`; `RpcSocket` | [Sockets](integrations/websockets/05-sockets.md), [binary streams](integrations/websockets/04-streams.md) |
| Shared WebSocket connection options | `APIRouter(websocket_policy=...)` with `fastapi_canon.integrations.websockets.WebSocketPolicy` | [Connection policy](20-routers.md#share-websocket-connection-policy) |
| WebRTC with Canon admission errors | `fastapi_canon.integrations.webrtc.WebRtcRouter` and `@rtc.session()`, `Session`, `SessionCapacity`; engine adapters in `.webrtc.engines` | [WebRTC admission](11-webrtc.md) |
| Contract export | `fastapi_canon.export_contracts`; `fastapi_canon.contracts.Contracts`, `ContractSelection`; `fastapi-canon contracts export` | [Application export](22-contracts.md) |
| OpenAPI contracts without `Composition` | `fastapi_canon.openapi.install_openapi_contracts`, `validate_openapi_contracts` | [Composition](05-composition.md) |
| Startup configuration errors | `fastapi_canon.errors.ConfigurationError` and its subclasses | [Problems](02-errors.md) |
| Telemetry declarations | `fastapi_canon.integrations.opentelemetry.Telemetry`, `instrument`, `ResourceInstrumentation`, `SdkProviders`, `SdkFactory`, `console` | [Telemetry](10-observability.md) |
| HTTP client and engine instrumentation | `HttpxClients`, `instrument_httpx` in `.opentelemetry.httpx`; `SqlAlchemyEngines`, `instrument_sqlalchemy` in `.opentelemetry.sqlalchemy` | [Resources](10-observability.md#useful-options) |
| MCP features | `fastapi_canon.integrations.mcp.McpServer`, `McpMount`, `McpScope`, `Mcp`, `Inject` | [MCP](12-mcp.md) |
| Custom integrations | `fastapi_canon.integration.Integration`, `IntegrationPart`, `IntegrationContext`, `Installer`, `IntegrationError`, `IntegrationRoute`, `RouteContext`, `ContractExporter`, `attach_route` | [Integrations](05-composition.md#integrations) |
| Generator extensions | `fastapi_canon.codegen.generators.Generator`, `TargetConfig`, `Rendering` | [Generator registration](06-codegen.md#go-deeper) |

Import application-building names from `fastapi_canon` and optional types from
their integration facade. Other implementation modules are internal, even without
a leading underscore, and may change in any release. Run the source checker with
`python -m fastapi_canon check errors`; see
[error checking](07-error-checking.md).

```python
from fastapi_canon import APIRouter, Composition, Feature, Inject
from fastapi_canon.integrations.opentelemetry import Telemetry, instrument
from fastapi_canon.integrations.websockets import RpcChannel
```

Composition owns the application Dishka container and feature installation.
Applications own authentication, business resources and telemetry providers.
See [composition](05-composition.md) and the integration guides for lifecycle rules.

In Canon 0.21.0 and later, optional arguments select their default only for
`None`. Explicit empty values and callable objects with false truthiness retain
their meaning; for example, `message=""` preserves an empty error message and
an empty path-parameter mapping overrides the handshake parameters.

## Commands and generated artifacts

| Command | Responsibility |
| --- | --- |
| `fastapi-canon contracts export` | Export registered application contracts |
| `fastapi-canon generate` | Run a version-2 workflow of specialized and external generators |
| `fastapi-canon sse` | Export and generate specialized stream clients |
| `fastapi-canon websockets` | Export OpenRPC and generate RPC/socket clients |
| `fastapi-canon webrtc` | Export media contracts and generate TypeScript clients |

The 0.17.0 workflow uses separate transport clients and external HTTP
tooling. Use `generate --check --diff` for drift, `--target NAME` to select a
client and `--offline` for saved contracts. See [configuration and migration](06-codegen.md).

Use [contributing](90-contributing.md) for development commands and
[architecture](91-architecture.md) for repository boundaries.
