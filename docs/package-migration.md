# One Canon distribution

The 0.10.0 release includes composition, HTTP contracts, SSE, WebSocket
RPC/sockets, WebRTC and their client generators in one `fastapi-canon` wheel.
Earlier independent distributions remain available at their existing published
versions.

Development and future releases now use
[`mathisarends/fastapi-canon`](https://github.com/mathisarends/fastapi-canon).
The [repository migration](repository-migration.md) records the
source history and publishing setup. The repository move does not rename the
PyPI project or add further Python import changes.

## Upgrade to 0.16.0

Version 0.16.0 was published on 2026-11-07. It contains
breaking server APIs, generated-client interfaces and stricter preflight/static
checks. Upgrade servers and regenerated Python/TypeScript SDKs together. The
[complete breaking-change notes](../CHANGELOG.md#0160---2026-11-07) include the
individual signatures and diagnostic coverage boundaries.

### Composition and public imports

`Composition.build(app)` replaces `apply(app)` and `validate(app)`:

```python
# Before
app = composition.apply(FastAPI())
composition.validate(app)

# After
app = composition.build(FastAPI())
```

Register plain FastAPI routes before `build`, or call `build` again after adding
them; rebuilding the same composition only repeats validation.

Move a shared `Composition(router_policy=policy)` to the application router:

```python
composition = Composition(
    features=[projects],
    router_factory=lambda: APIRouter(prefix="/api", policy=policy),
)
```

Parent router contracts now extend included HTTP/SSE operations. Move errors
that should stay local onto individual routes or a sibling router. Use unique
effective HTTP method/path combinations, WebSocket paths and operation IDs;
composition rejects ambiguous registrations before installation.

Import `APIRouter`, `Error`, `RouterPolicy` and `Inject` from `fastapi_canon`.
Integration-specific declarations such as `RpcChannel` come from their
`fastapi_canon.integrations.*` facade; see [public imports](canon.md).
Replace `ExceptionHandlerSpec` sequences with
`Feature(exception_handlers={DomainError: handler})` and iterate the mapping's
`.items()` when inspecting handlers.

### Error declarations

Replace SSE `stream_raises=` with `raises=`.
When both arguments were present, combine their definitions in one sequence:

```python
@router.sse(
    "/events",
    raises=[authentication_required, task_not_found],
)
async def events(...) -> AsyncIterator[TaskEvent]: ...
```

`raises` now registers canonical errors automatically on HTTP routers, policies,
SSE routes, WebRTC admission and RPC channels/operations. Remove explicit registry
entries that only repeat these declarations. List additional errors that are
not declared anywhere with `Feature(errors=[...])`.
Set the common URI prefix with `Composition(errors=ErrorOptions(type_base=...))`.

For SSE, the combined effective declaration describes both HTTP admission
responses and terminal `canon.problem` frames. Dependencies run admission checks
before response start; generator code, even before its first `yield`, runs after
the response begins. Regenerate SDKs to include both representations of the
combined error contract. Existing explicit `Error.type` URIs remain authoritative.

`Feature.errors` is now a tuple of `Error` instances: replace
`feature.errors.errors` with `feature.errors`, and `.contains(error)` with
`error in feature.errors`. Dependencies use explicit router/policy/operation
contracts; there is no `@raises` dependency decorator.

### Routes, transports and dependency lifetimes

Replace facade imports of `CanonStreamRouter`/`StreamRouter` with `APIRouter` and
`@router.sse(...)`. Replace `CanonWebRtcRouter`/`WebRtcRouter` with
`@router.webrtc(...)`; move engine, ICE, origin and limit settings to each
declaration. Use a shared `SessionCapacity` for a budget spanning declarations.
Engine adapter types are exported from `fastapi_canon.integrations.webrtc.engines`.

HTTP/SSE operation IDs now default to the function name, optionally prefixed by
the router's `namespace`. Preserve an existing SDK name with an explicit
`operation_id`, or retain FastAPI's old policy through
`generate_unique_id_function=fastapi.utils.generate_unique_id`. OpenAPI tags no
longer determine common SDK groups. See [operation naming](routers.md#automatic-http-and-sse-operation-names).

`APIRouter` resolves endpoint `Inject[T]` and `FromDishka[T]` automatically;
ordinary dependency functions support `Inject[T]` without `@inject`. Keep
`@inject` for dependency functions using `FromDishka[T]`. Register missing
bindings reported by composition preflight before adopting the release.
HTTP REQUEST resources close before response transmission. SSE bodies need APP
services or independent short `ServiceScope` calls; WebSocket/WebRTC resources
retained through disconnect belong to SESSION providers. Admission hooks return
independently usable context values. See [dependency coverage](system-checks.md).

### RPC models and generated SDKs

RPC now preserves plain Pydantic models' own aliases, validation and extra-field
policy. Inherit from `RpcModel`, or explicitly configure the previous camel-case
and `extra="forbid"` policy, when retaining the old RPC convention. Regenerate
clients for the declared wire shape. Shared DTOs need compatible schemas and one
semantic identity; see [shared models](unified-canon-concept.md).

Regenerate both SDK languages through `fastapi-canon generate`. Existing source
tables stay supported; `[codegen] app = "module:app"` can now export the app's
contracts and select the supported sources for each target automatically.

- Import canonical error classes from the SDK root. Common HTTP, SSE and RPC
  calls share those classes; replace binding-private error imports. Problem
  `title` is public text rather than a literal discriminator: branch on error
  class or problem `type`.
- Replace TypeScript `RequestOptions` with `CallOptions`, `HttpOptions` or
  `StreamOptions`. SSE deadlines use `openTimeoutMs`/`idleTimeoutMs`. Methods
  without parameters take options directly, without an initial `{}`. Deadlines
  raise native `TimeoutError` DOMExceptions; cancellation preserves its reason.
- An omitted Python `RequestOptions.timeout` inherits the injected HTTP client's
  configuration. Pass `timeout=None` to disable it. Use the generated `Bindings`,
  `Clients` and per-source connection option types for wrapper dictionaries.
- Remove consumer imports of protocol envelopes and implementation aliases from
  the common TypeScript root. Use its reachable domain/problem models. Node ESM
  imports use `.js`; explicit resource management requires `ESNext.Disposable`
  types. See [SDK scope](unified-canon-concept.md#connections-and-supported-scope).
- Preserve the optional initialization `signal` in TypeScript RPC connection
  wrappers. Rename a server URL variable named `signal` before generation.
  Closing a common SDK cancels pending eager handshakes while individual call
  cancellation leaves shared initialization available to sibling calls.

### Static checks and CLI integrations

Run source checks before enabling strict CI on the upgraded application. Newly
followed provider implementations, local router factories, context managers,
loops and comprehensions can expose existing CAN001/CAN002 gaps. Declare/map
those errors explicitly; CAN003 still reports incomplete analysis.

Use synchronous, inspectable zero-argument application factories. CLI import and
factory failures omit arbitrary exception text; automation should consume the
documented JSON diagnostic codes and location fields. Review unsupported callable
forms and the per-check coverage report before enforcing `--require-complete`.

## Upgrade to 0.15.0

The new MCP integration is opt-in: install `fastapi-canon[mcp]`, contribute
`McpMount` declarations through `Feature(mcp=[...])`, and activate them with
`Composition(mcp=McpOptions())`. Core installations do not require FastMCP.
Mounted routes use FastMCP authentication configured on `McpOptions`, independently
of FastAPI router dependencies. See the [MCP guide](integrations/mcp/README.md)
for `Inject[T]`, lifecycle ownership and supported handlers.

Replace `@channel.subscription()` with `@channel.stream()`. Bare `bytes` items
select binary frames; other supported item types select JSON messages. Uploads
and duplex binary handlers retain `RpcBinaryInput` and `RpcBinaryOutput`.
Replace `RpcLimits(max_subscriptions=...)` with `RpcLimits(max_streams=...)`.

Regenerate contracts and both SDKs together with the server upgrade. The new
wire controls are `.open` / `.cancel`, their identity field is `streamId`, and
OpenRPC uses `x-rpc-streams`. There are no aliases for the old decorator,
control names or contract extension. Generated TypeScript stream methods must
now be awaited; prefer `await using stream = await client.jobs.progress(...)`.
Python supports `async with client.jobs.progress(...) as stream` and iteration
inside the block. The common domain SDK uses the same managed resource pattern
for JSON-RPC message streams. See the [full stream guide](integrations/websockets/message-streams.md).

Reconnect remains opt-in and reopens the source with a fresh ID. It does not
replay missed data or resume a computation automatically. Existing credential
refresh, recovery limits, middleware, graceful drain and binary upload/duplex
APIs remain available. Message-stream overflow now fails explicitly; passive
notification overflow policies and counters remain configurable.

## Upgrade to 0.14.0

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
   `engine_context=SqlAlchemyInstrumentation(telemetry=telemetry,
   pool_name="backend")`. Import the declaration from
   `fastapi_canon.integrations.opentelemetry.sqlalchemy` and select
   `otel-sqlalchemy` for that adapter. Any synchronous context-manager factory can
   use the hook without coupling database ownership to telemetry.
3. Replace direct Alembic rollback adapters with
   `await migrations.downgrade(engine, revision)` or
   `fastapi-canon db downgrade <revision>`. Supply an explicit target. Current
   application startup and checks still expect its heads; an older schema needs
   compatible application code and an explicit deployment validation policy.

See the [database guide](database.md) and
[breaking changes](../CHANGELOG.md#0140---2026-11-03) for lifecycle and validation
semantics.

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

## Agent skill

From your application project, install the bundled backend-building skill:

```console
uv run fastapi-canon skill install
```

This copies the skill and its references to `.agents/skills/build-canon-backend`
and `.claude/skills/build-canon-backend`. To update both from your installed Canon
version:

```console
uv sync --upgrade-package fastapi-canon
uv run fastapi-canon skill install --force
```

`--force` replaces the complete destination directories, including local edits.
Keep custom guidance elsewhere. Use `--destination <skill-directory>` for a
single custom destination. Installation uses the installed Canon bundle and
does not download another skill version.

## Python imports

| Earlier import | Canon import |
| --- | --- |
| `fastapi_streams` | `fastapi_canon.integrations.sse` |
| `fastapi_rpckit` | `fastapi_canon.integrations.websockets` |
| `fastapi_webrtc` | `fastapi_canon.integrations.webrtc` |
| `fastapi_canon.integrations.streams` | `APIRouter.sse()` |

Declare SSE streams with `APIRouter.sse()` and WebRTC sessions with
`APIRouter.webrtc()`; the stream and session routers are internal since 0.16.0.
WebRTC value types such as `Session` and `SessionCapacity` remain in
`fastapi_canon.integrations.webrtc`, and engine adapter types live in
`fastapi_canon.integrations.webrtc.engines`.
In 0.13.0, use `CanonWebSocketRouter` instead of the earlier service and adapter
factories; follow the [WebSocket migration guide](integrations/websockets/migration.md).
Use `fastapi_canon.__version__` for the single Canon version.

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
and owned output directory. SSE uses `[tool.fastapi-canon.sse]`; its former
`[tool.fastapi-streams]` table remains accepted for one transition release with
a deprecation warning. WebSocket `rpcgen.toml` is retained. Update command names
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
