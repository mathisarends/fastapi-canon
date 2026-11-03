# One Canon distribution

The 0.10.0 release includes composition, HTTP contracts, SSE, WebSocket
RPC/sockets, WebRTC and their client generators in one `fastapi-canon` wheel.
Earlier independent distributions remain available at their existing published
versions.

Development and future releases now use
[`mathisarends/fastapi_canon`](https://github.com/mathisarends/fastapi_canon).
The [repository migration](repository-migration.md) records the
source history and publishing setup. The repository move does not rename the
PyPI project or add further Python import changes.

## Upgrade to 0.14.0 (unreleased)

The database integration remains opt-in. Select the dependencies used by your
application explicitly:

| Install | Dependencies added |
| --- | --- |
| `fastapi-canon` | Dishka, FastAPI and Pydantic |
| `fastapi-canon[database]` | Async SQLAlchemy and AnyIO |
| `fastapi-canon[sqlite]` | Database dependencies and aiosqlite |
| `fastapi-canon[postgres]` | Database dependencies and asyncpg |
| Extra `sqlmodel` | Database dependencies and SQLModel |
| Extra `migrations` | Database dependencies and Alembic |

These dependencies were already optional in 0.13.0. Existing compatible explicit
dependencies also work; extras provide the corresponding dependency selection.
All integration code ships in Canon's wheel. PostgreSQL itself is operated
separately. `ServiceScope` has no database dependency. Keep custom engine/session
providers and migration lifespans when your application owns those resources.

When adopting the database integration:

1. Keep startup migration validation enabled by default. If deployment owns these
   checks, set `Database(..., migrations=migrations,
   check_migrations_on_startup=False)` and retain `fastapi-canon db check` in that
   deployment. One declaration now serves both runtime and CLI.
2. An engine instrumentation Dishka decorator can become
   `engine_context=lambda engine: instrument_sqlalchemy(engine, telemetry,
   pool_name="backend")`. Select `otel-sqlalchemy` for that adapter. Any synchronous
   context-manager factory can use the hook without coupling database ownership
   to telemetry.
3. Replace direct Alembic rollback adapters with
   `await migrations.downgrade(engine, revision)` or
   `fastapi-canon db downgrade <revision>`. Supply an explicit target. Current
   application startup and checks still expect its heads; an older schema needs
   compatible application code and an explicit deployment validation policy.

See the [database guide](database.md) and
[breaking changes](../CHANGELOG.md#0140---unreleased) for lifecycle and validation
semantics. This is an unreleased API change targeting the next minor version.

## Upgrade to 0.13.0

Upgrade from 0.12.0 to 0.13.0 with these application changes:

1. Replace positional composition arguments with `Composition(features=[first,
   second])`. All constructor options are keyword-only.
2. Replace `RpcService`, `RpcRoutes`, `create_router()` and `dishka_router()` with
   `CanonWebSocketRouter`. Include it directly in `Feature(routers=[router])`,
   and pass `resolver=Dishka()` for Dishka injection. Replace
   `Telemetry.rpc_middleware()` with `Telemetry.websocket_middleware()`.
   Earlier development snapshots named the new router `RpcRouter`; that name
   is also removed. See the [WebSocket migration guide](integrations/websockets/migration.md).
3. Require FastAPI >=0.121. HTTP dependency scopes now close after response
   validation and before sending the response. Open an independent
   `ServiceScope` for streams, workers and background tasks instead of retaining
   request-owned resources. See [scope ownership](database.md).
4. Keep the ASGI server's exception logger enabled for unhandled HTTP failures;
   Canon no longer emits a second traceback for the same failure. Configure
   [request logging](request-logging.md) explicitly if completion records are needed.
5. Update telemetry queries that assumed every call on a WebSocket shared one
   trace. Calls without message context now start independent traces linked to
   the connection. Explicit message context still supplies the parent.

The JSON-RPC wire format and generated-client contracts remain unchanged by the
router and tracing changes. Database integration and request logging are opt-in;
follow the [database guide](database.md) when adding engines, transactions or
Alembic migrations. See the [full changelog](../CHANGELOG.md#0130---2026-11-02)
for the release's migration details.

## Installation

```console
uv add "fastapi-canon[sse,websockets,webrtc]"
uv add --dev "fastapi-canon[codegen]"
```

For the reference WebRTC engine, also select `aiortc`:

```console
uv add "fastapi-canon[webrtc,aiortc]"
```

Every install contains all Python feature modules and generator templates.
Extras install the dependency versions needed by a feature; they do not install
another Canon distribution. SSE selects FastAPI >=0.143.3 and Pydantic >=2.11.2,
while base composition requires FastAPI >=0.121 and Pydantic >=2.9 in 0.13.0.
The published 0.12.0 base requires FastAPI >=0.115. WebSockets
selects its Pydantic and typing requirements. WebRTC selects its AnyIO, Starlette
and typing requirements. `codegen` selects the template/schema tooling and model
dependencies needed to generate clients from saved contracts. It does not
require an application import or the aiortc engine.

Dishka is a base dependency. Python server WebSockets use FastAPI/Starlette;
the `websockets` Python client package is only needed by generated client
applications that select that transport. Generated SSE consumers install their
selected HTTP backend (`httpx` or `httpx2`) separately. `otel` selects FastAPI >=0.143 and OpenTelemetry API >=1.40,<2
for native HTTP telemetry, Dishka port observations and WebSocket middleware. Application
providers/exporters remain application-owned. See the [telemetry guide](telemetry.md).

Remove the old `fastapi-streams`, `fastapi-rpckit` and `fastapi-webrtc` direct
dependencies from your application. Replace Canon's former `streams` extra with
`sse`. There are no imports forwarded to the earlier distributions.

## Python imports

| Earlier import | Canon import |
| --- | --- |
| `fastapi_streams` | `fastapi_canon.integrations.sse` |
| `fastapi_rpckit` | `fastapi_canon.integrations.websockets` |
| `fastapi_webrtc` | `fastapi_canon.integrations.webrtc` |
| `fastapi_canon.integrations.streams` | `fastapi_canon.integrations.sse` (`CanonStreamRouter`) |

Public classes such as `StreamRouter`, `RpcSocket`, `WebRtcRouter`,
`CanonStreamRouter` and `CanonWebRtcRouter` live inside the integration namespaces.
In 0.13.0, use `CanonWebSocketRouter` instead of the earlier service and adapter
factories; follow the [WebSocket migration guide](integrations/websockets/migration.md).
Feature `__version__` exports refer to the single
Canon version. The published `fastapi_canon.integrations.webrtc` admission-router
import remains available and now contains the complete WebRTC integration.

WebSocket framework adapters live under
`fastapi_canon.integrations.websockets.adapters`, for example `.adapters.dishka`.
Replace the former `fastapi_rpckit.integrations` prefix with that path.
Import the optional media adapter from
`fastapi_canon.integrations.webrtc.engines.aiortc`. WebSocket logger filters
should use `fastapi_canon.integrations.websockets`.

Earlier prepared snapshots used `fastapi_canon.sse`, `.websockets` and `.webrtc`.
Replace those short paths with `fastapi_canon.integrations.sse`, `.websockets`
and `.webrtc`. There are no parallel short namespace aliases. See the
[canonical API](canon.md) for the public namespace map.

## Commands

In 0.10.0, replace the previous `canon` executable with `fastapi-canon` in scripts
and CI. Subcommand options stay the same.

```console
uv run fastapi-canon --help
uv run fastapi-canon --version
uv run fastapi-canon check errors src --strict

uv run fastapi-canon sse schema --app app.main:app --output contracts/openapi.json
uv run fastapi-canon sse generate --schema contracts/openapi.json --language typescript --output frontend/generated/sse
uv run fastapi-canon sse sync --config pyproject.toml --check --diff

uv run fastapi-canon websockets schema app.contracts:contract --output contracts/openrpc.json
uv run fastapi-canon websockets generate --config rpcgen.toml --check

uv run fastapi-canon webrtc contract --app app.main:app --output contracts/webrtc.json
uv run fastapi-canon webrtc generate --contract contracts/webrtc.json --output frontend/generated/webrtc --check
```

`python -m fastapi_canon` accepts the same arguments as `fastapi-canon`. Each transport
retains its existing command options, strict diagnostics, configuration format
and owned output directory. SSE's `[tool.fastapi-streams]` table and WebSocket
`rpcgen.toml` are retained for this first packaging step. Update command names
and application imports, then regenerate all clients to update their manifests.
Binding plugins must declare `fastapi_canon.integrations.websockets.socket_bindings` entry
points and import the Canon binding APIs.

The packaging migration retains WebRTC signaling identifiers and existing
output ownership manifests. Canon 0.10.0 adds the shared application-error
bindings and generated-client changes described in [canonical errors](problems.md).
Regenerate clients and follow that guide when adopting the new stream profile.

## Common codegen workflow

Canon 0.10.0 added version-1 `fastapi-canon generate` configuration, shared model/error
identity and a domain SDK facade. JSON HTTP calls, SSE, JSON-RPC and sessions can
contribute to one output per language. Put generation settings in `[codegen]` in
`fastapi-canon.toml`, or `[tool.fastapi-canon.codegen]` in `pyproject.toml`. Follow the
[codegen guide](unified-canon-concept.md) and choose a fresh output
when adopting the common workflow. Specialized generator commands remain
available with their own configuration and ownership; never point multiple
generators at the same SDK directory.
