# Canon package and public API

Canon is one `fastapi-canon` distribution and one `fastapi_canon` import package.
Integrations share its version; extras select optional dependencies.
The base requires FastAPI, Pydantic and Dishka. Core imports and CLI help work
without codegen dependencies, telemetry runtimes or media engines.

These docs follow the checkout. See [releases](releases.md#current-versions) for
published and prepared versions and [compatibility](compatibility.md) for tested
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
| `otel-httpx`, `otel-sqlalchemy` | Explicit per-client HTTPX and per-engine SQLAlchemy telemetry adapters |
| `codegen` | Template, schema-validation and model dependencies for generators |
| `database` | Async SQLAlchemy engine and session ownership, without a driver |
| `sqlite`, `postgres` | Database integration with aiosqlite or asyncpg |
| `sqlmodel` | SQLModel sessions on the same database integration |
| `migrations` | Alembic revision generation, upgrades and schema checks |

## Public namespaces

| Responsibility | Public imports | Guide |
| --- | --- | --- |
| Shared application router and composable channels | `fastapi_canon.APIRouter`, `fastapi_canon.integrations.websockets.RpcChannel` | [One feature router](routers.md) |
| Application composition and Dishka lifecycle | `fastapi_canon.Composition`, `Feature` | [Composition](composition.md#feature-composition) |
| Typed transport dependencies and context hooks | `fastapi_canon.Inject` | [Dependency injection](integrations/websockets/dependencies.md) |
| Composition checks and installed HTTP descriptions | `Composition.check(app)`, `fastapi_canon.inspect_app(app)`; values `fastapi_canon.diagnostics.CheckReport`, `CheckIssue`, `CheckContext`, `CompositionInfo`, `FeatureInfo`, `OperationInfo` | [System checks](system-checks.md) |
| Database ownership, migrations and locks | `fastapi_canon.integrations.database.Database`, `AlembicMigrations`, `AcquireTransactionLock` (`TransactionLocks` alias) | [Databases](database.md) |
| Short service scopes | `fastapi_canon.Scoped`, `ServiceScope` | [Databases](database.md) |
| HTTP routes and contracts | `fastapi_canon.APIRouter`, `RouterPolicy`, `Error`, `ErrorOptions`, `CanonResponse`; `Problem`; `fastapi_canon.error.ErrorRegistry` for manual assembly without `Composition` | [HTTP contracts](composition.md#error-contracts) |
| Typed SSE | `APIRouter.sse()` | [SSE contracts](streams.md) |
| WebSocket JSON-RPC | `APIRouter.channel()`; `APIRouter.include_channel()` with `fastapi_canon.integrations.websockets.RpcChannel` for shared channels | [WebSockets](integrations/websockets/README.md) |
| Typed or raw WebSocket messages and binary streams | `@APIRouter.socket()`, `@APIRouter.binary_stream()`; `RpcSocket` | [Sockets](integrations/websockets/sockets.md), [binary streams](integrations/websockets/streams.md) |
| WebRTC with Canon admission errors | `APIRouter.webrtc()`, `fastapi_canon.integrations.webrtc.Session`, `SessionCapacity`; engine adapters in `.webrtc.engines` | [WebRTC admission](webrtc.md) |
| Contract export | `fastapi_canon.export_contracts`; `fastapi_canon.contracts.Contracts`, `ContractKind`, `ContractSelection`; `fastapi-canon contracts export` | [Application export](contracts.md) |
| OpenAPI contracts without `Composition` | `fastapi_canon.openapi.install_openapi_contracts`, `validate_openapi_contracts` | [Composition](composition.md) |
| Startup configuration errors | `fastapi_canon.exceptions.ConfigurationError` and its subclasses | [Problems](problems.md) |
| Telemetry declarations | `fastapi_canon.integrations.opentelemetry.Telemetry`, `instrument` | [Telemetry](telemetry.md) |
| SQLAlchemy engine instrumentation declaration | `fastapi_canon.integrations.opentelemetry.sqlalchemy.SqlAlchemyInstrumentation` | [Engine lifetime](database.md#engine-instrumentation-lifetime) |
| MCP features | `fastapi_canon.integrations.mcp.McpServer`, `McpMount`, `McpScope`, `McpOptions`, `Inject` | [MCP](integrations/mcp/README.md) |

Import application-building names from `fastapi_canon` and optional types from
their integration facade. Other implementation modules are internal, even without
a leading underscore, and may change in any release. The source checker also
supports `python -m fastapi_canon.check_error_contracts`; see
[error checking](error-checking.md).

```python
from fastapi_canon import APIRouter, Composition, Feature, Inject
from fastapi_canon.integrations.opentelemetry import Telemetry, instrument
from fastapi_canon.integrations.websockets import RpcChannel
```

Composition owns the application Dishka container and feature installation.
Applications own authentication, business resources and telemetry providers.
See [composition](composition.md) and the integration guides for lifecycle rules.

In the prepared 0.21.0 release, optional arguments select their default only for
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
client and `--offline` for saved contracts. See [configuration and migration](codegen.md).

Use [contributing](contributing.md) for development commands and
[architecture](architecture.md) for repository boundaries.
