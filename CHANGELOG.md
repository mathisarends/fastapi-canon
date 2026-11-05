# Changelog

All notable changes to `fastapi-canon` are documented in this file.

## Unreleased

### Added

- A shared root `APIRouter` with typed `sse(...)`, reusable
  `include_channel(..., path=...)` and managed `webrtc(...)` declarations.
  `RpcChannel()` supports anonymous groups and snapshot-based
  `include_channel(..., namespace=...)` composition. Existing specialized routers,
  wire controls and generated-client runtimes remain supported. See
  [the shared router guide](docs/routers.md).

- Public `fastapi_canon.export_contracts(app)` and `Contracts` snapshot API for
  HTTP/SSE OpenAPI, mounted RPC OpenRPC and WebRTC session contracts. RPC paths
  come from the registered routes, including nested router and mount prefixes;
  custom handlers keep their declarations. Unmounted operations are omitted and
  ambiguous endpoints, operation names and protocol versions fail explicitly.
- `fastapi-canon contracts export --app module:create_app --output-dir
  contracts`, automatically accepting an existing app or invoking a synchronous
  zero-argument app factory, with format selection, public RPC origin,
  deterministic JSON and read-only `--check`/`--diff`. It needs no SDK target
  configuration and never
  enters application lifespan or resolves runtime dependencies. See
  [application contracts](docs/contracts.md) for factories without placeholder
  secrets and the distinction between runtime and contract-affecting settings.
- Common SDK generation accepts the same FastAPI app/factory as an OpenRPC source
  and reuses one factory result across all source formats and targets. Bindings
  from these new app contracts inherit the SDK's public base URL. Offline
  generation continues to consume saved contracts without importing application code.

### Breaking changes

- Composed applications now install the configured validation, HTTP-exception
  and unhandled-error normalizers even without a domain registry. The fallback
  problem type base is `urn:fastapi-canon:problem`. To retain application-owned
  framework handlers, disable the corresponding normalizers through `ErrorOptions`;
  an already installed registry must use the same options as the composition.
- Install compositions and error registries before application startup. OpenAPI
  is validated before application resources open, and operation IDs must be unique.
  Explicit feature registries supplement automatically collected router registries.
- Normalized validation and internal failures participate in the documented
  422/500 problem unions. Regenerate SDKs when adopting these response contracts.
  These changes require the next minor release when this unreleased work ships.

### Migration

- Replace application-specific OpenAPI scripts and handwritten mounted RPC URL
  maps with the application export command or root Python API. Existing transport
  commands and standalone `RpcContract` sources remain supported; this addition
  does not require a breaking API or wire-format change.

## 0.15.0 - 2026-11-04

### Added

- Optional `fastapi-canon[mcp]` integration: explicit feature `McpMount` contributions,
  application `McpOptions` with `/mcp` and app-title defaults, and FastMCP-backed
  `McpServer` registration with compact Dishka `Inject[T]` annotations. Composition
  owns the MCP lifespan and isolated per-execution dependency cleanup.
- Installed-wheel and public-index smoke checks cover the optional MCP extra,
  including an HTTP tool call, injected input schemas and request-scope cleanup.

### Breaking changes

- Replace `@channel.subscription()` with `@channel.stream()`. The old decorator
  is removed without an alias. `AsyncIterator[T]` declares JSON message streams;
  `AsyncIterator[bytes]` declares binary frames. Binary input/output ports retain
  upload and duplex support. Use `content_type` and `input_content_type` only for
  binary transfers; message streams keep `raises=` and channel error declarations.
  Unconstrained `Any` and mixed raw-byte/message payloads fail at registration.
- Replace message-stream wire controls `<operation>.subscribe` and
  `<operation>.unsubscribe` with `<operation>.open` and `<operation>.cancel`.
  Control acknowledgements, data and terminal messages now use `streamId`.
  OpenRPC describes the operation in `x-rpc-streams`, including framing,
  direction, overflow and reconnect policy. Old control names and the old
  `x-rpc-subscriptions` contract are not accepted. Upgrade server and clients
  together and regenerate both language targets.
- Rename `RpcLimits.max_subscriptions` to `max_streams`; the default remains 100
  logical message streams per connection. The limit error code is now
  `stream_limit`. Binary sockets keep their existing
  independent lifecycle and limits.
- Generated Python message operations return an iterable stream resource. Prefer
  `async with client.jobs.progress(job_id=...) as stream:` followed by `async for`;
  entering opens the operation before waiting for its first value and leaving
  cancels it with cleanup. Direct iteration and explicit `aclose()` also work.
  Generated TypeScript message operations now return `Promise<RpcStream<T>>`:
  use `await using stream = await client.jobs.progress({ jobId: ... })` and
  `for await`. `close()` and loop exit stop that stream. Binary receive operations
  keep the same resource pattern. The common domain SDK also exposes managed
  resources for JSON-RPC message streams.
- Message-stream queue overflow is an explicit transport/consumer error rather
  than silent truncation. Passive notification overflow policies and counters
  remain available. Reconnect is still opt-in: `reconnect=True` / `reconnect: true`
  reopens active message streams with fresh IDs, on the replacement transport;
  old IDs are cancelled only on their issuing transport. Reopening restarts the
  source and does not guarantee replay or exact continuation. Retry deadlines,
  credential refresh, connection recovery, scoped errors, drain and binary
  backpressure remain supported.

## 0.14.0 - 2026-11-03

### Breaking changes

- Extend the public `Database` declaration with `check_migrations_on_startup`
  (default `True`) and `engine_context`. Deployments that own migration execution
  and validation can set `check_migrations_on_startup=False` on the same declaration
  used by the CLI. Explicit `db check` remains available and strict. Replace an
  engine instrumentation Dishka decorator, if desired, with
  `engine_context=SqlAlchemyInstrumentation(telemetry=telemetry,
  pool_name="backend")`, imported from
  `fastapi_canon.integrations.opentelemetry.sqlalchemy`. The immutable declaration
  creates a fresh context for each engine and exits before pool disposal,
  including failed startup. Custom context factories remain supported.
- Extend `AlembicMigrations` and `fastapi-canon db` with explicit
  `downgrade(engine, revision)` and `db downgrade <revision>`. Supply the target
  explicitly. Older targets are not compared with current model metadata;
  `require_current()`, `db check` and default startup validation still require
  application heads. Keep application-owned Alembic downgrade revisions and use
  application code compatible with the target schema.
- Make database installation and adoption requirements explicit in migration
  documentation: base `fastapi-canon` selects Dishka, FastAPI and Pydantic;
  `[database]` adds async SQLAlchemy and AnyIO; `[sqlite]` adds aiosqlite;
  `[postgres]` adds asyncpg; `sqlmodel` and `migrations` add SQLModel and Alembic
  respectively, on top of database dependencies. Select the extras your
  application uses, for example `fastapi-canon[postgres,sqlmodel,migrations]`.
  These dependencies were already optional in 0.13.0; this documents the required
  installation choices rather than introducing a dependency removal. Integration
  code remains in the same wheel. Applications can retain their own providers
  and lifespans; the database integration is opt-in, and `ServiceScope` remains
  independent of SQLAlchemy and Alembic.

### Added

- Bundle the progressive `build-canon-backend` skill with HTTP, resource,
  telemetry, transport and testing guides. Install it into a consumer project
  with `fastapi-canon skill install`; use `--destination` for a custom location.

## 0.13.0 - 2026-11-02

### Breaking changes

- Rename `RpcRouter` to `CanonWebSocketRouter` in imports and constructors, and
  `Telemetry.rpc_middleware()` to `Telemetry.websocket_middleware()`. The old
  names are removed without aliases. JSON-RPC channel, wire and generated-client
  contracts, including the established `rpckit.*` metric names, are unchanged.
- WebSocket controller calls without per-message trace metadata now start
  independent traces and link to the connection context instead of inheriting
  its trace. Explicit message context still parents the call, and handler work
  remains nested under it. Update trace queries that assumed all calls on one
  connection shared a trace ID. No client instrumentation is required.

- WebSocket server composition now uses `CanonWebSocketRouter` exclusively. Replace
  `RpcService` plus `create_router(service)` or `dishka_router(service)` with
  `CanonWebSocketRouter` and include it directly in `Feature(routers=[rpc])` or FastAPI.
  Replace `RpcRoutes(router, ...).mount(endpoint)` with options on `CanonWebSocketRouter`
  and its `socket()` or `stream()` declaration. Use `resolver=Dishka()` for
  Dishka, `context=async_dependency` for a connection context, and `serve=`
  for custom FastAPI handlers. The removed names have no compatibility aliases.
  See the WebSocket migration guide.

- `Composition` now accepts features only through the keyword-only
  `features: Sequence[Feature] = ()` option. Replace `Composition(tasks, notifier)`
  with `Composition(features=(tasks, notifier))`, or pass a list with
  `Composition(features=[tasks, notifier])`. Replace `Composition(*features, ...)`
  with `Composition(features=features, ...)`. Omission still means no features;
  declaration order, duplicate checks and the immutable tuple snapshot are
  preserved. Positional feature arguments now raise `TypeError`.
- Unhandled HTTP exceptions are diagnosed by the ASGI server after Starlette
  sends Canon's safe 500 response. Applications relying on the additional
  `fastapi_canon.error.handlers` traceback should retain `uvicorn.error` and route
  it through one application log handler. Use `Telemetry(logs=False)` when that
  server diagnostic should be the only exception log. Internally consumed
  response-validation and rendering failures still retain their Canon diagnostic.
- Composition-owned HTTP dependency scopes now end after response validation and
  before the response is sent. Remove application dependencies that duplicate
  this scope management. Streams and background tasks must open their own short
  `ServiceScope` instead of retaining request-owned resources. The minimum
  FastAPI version is now 0.121 for function-scoped dependency cleanup. WebSocket
  session/request scope behavior is unchanged.

### Added

- `CanonWebSocketRouter(path, channels=[...])` mounts one JSON-RPC WebSocket endpoint
  as a FastAPI router. Use it in `Feature(routers=[...])` and call its
  `contract()` method to export OpenRPC for the same channels.
- `Composition(features=features, request_logging=True)` emits structured completion/abort
  records on `fastapi_canon.access`, including route templates, sent status,
  streaming response duration, Canon problem codes and available server-span IDs.
  It defaults to disabled and works without the Canon OTel extra.
- The reference telemetry demo exports ordinary Python service logs and request
  records through one OTLP path to Loki, with provisioned Loki/Tempo links in both
  directions for the distributed tasks-to-notifier request.

- Optional `Database` integration with SQLite/aiosqlite and PostgreSQL/asyncpg,
  app-owned engines, request-owned sessions, commit on successful scope exit,
  rollback on exceptions/cancellation, and SQLModel session support. SQLite
  connections enforce foreign keys and explicit transaction control.
- `ServiceScope[Service]` for workers and long-lived operations that need a
  fresh, short dependency scope using the same application providers.
- Application-owned Alembic revisions through `AlembicMigrations` and
  `fastapi-canon db init|revision|upgrade|check`. The application checks revision
  heads at startup; applying migrations remains an explicit deployment step.
- Typed custom-session factory aliases and injectable PostgreSQL transaction
  advisory locks. SQLite rejects unsupported advisory locking unless omission is
  explicitly configured. Owned table/column presence is checked even behind a
  current revision; existing schemas are repaired with forward migrations.

### Development

- Extend post-upload public-index verification to the database, SQLite,
  PostgreSQL, migrations and SQLModel extras, including SQLite transactions and
  Alembic upgrades, across twelve installation combinations.

### Documentation

- Add a consolidated 0.12-to-0.13 upgrade guide covering composition, WebSocket
  routing, HTTP scope ownership and telemetry migration.
- Align current dependency requirements, integration installation guides and
  the seventeen-combination installed-wheel matrix with the prepared release.
  Keep published-version records separate from unreleased source APIs.

## 0.12.0 - 2026-11-02

### Breaking changes

- Composition's Dishka containers now reserve `Telemetry` for the application
  configuration, in every contributed component. Remove custom `Telemetry`
  bindings; inject the composition's instance instead. Without composition
  telemetry, infrastructure receives `Telemetry.disabled()`. In standalone
  containers, explicitly add `options.provider()` (or
  `Telemetry.disabled().provider()`). SDK providers remain borrowed.
- `instrument(Port, OtherPort, ...)` now requires at least one positional port
  and always returns a tuple of immutable declarations, including for a single
  port. Shared `expected` and `component` options apply to every port in the call.
  Replace `Feature(instrumentation=[instrument(Port)])` with
  `Feature(instrumentation=instrument(Port))`. Combine ports with the same options
  in one call; use `instrumentation=[*instrument(FirstPort),
  *instrument(SecondPort, expected=(ExpectedError,))]` for distinct groups.
  Replace `instrument(port=Port)` with `instrument(Port)`.
- HTTP requests excluded by `Telemetry.exclude` now also suppress participating
  downstream OpenTelemetry instrumentation, such as HTTPX, for the request's
  execution context. Remove application middleware that duplicates this
  suppression or re-evaluates the same exclusion predicate. Native HTTP telemetry
  and Canon ports also respect inherited OpenTelemetry suppression; HTTP-only
  suppression leaves port instrumentation enabled. WebSocket/RPC exclusion
  configuration is unchanged. The `otel` extra now includes the OpenTelemetry
  instrumentation package for its public suppression helpers.
- Remove `fastapi_canon.testing.telemetry.SpanCapture` from the distribution.
  Applications should maintain their own trace-capture fixture using the
  OpenTelemetry SDKÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢s in-memory exporter; the repository fixture provides an example.

### Added

- Explicit, lifetime-scoped `instrument_httpx(client, telemetry)` and
  `instrument_sqlalchemy(engine, telemetry)` adapters in their documented
  integration modules. Both respect application-local signal settings and
  request suppression and remove only their own instrumentation. HTTPX/HTTPX2
  reuse the public contrib per-client API; SQLAlchemy uses engine-local event
  listeners without wrapping factories or changing global instrumentors.
- Opt-in `otel-httpx` and `otel-sqlalchemy` extras, including SQLAlchemy's asyncio
  dependencies for sync/async engine support. Resource and SDK lifecycle
  remains application-owned. URLs, SQL text and exception messages are omitted
  by default; `capture_url=True` and `capture_statement=True` explicitly opt into
  potentially sensitive text. Headers, bodies and SQL parameters are not captured.
- Engine pool observations independent of query/request suppression, plus
  installed-wheel FastAPI bridge checks against 0.143.0 and the locked version.
  Native configuration drift now fails before application installation.

### Development

- Build Canon directly from the repository root. Source lives in `fastapi_canon/`;
  tests, examples and integration guides no longer live in separate package
  projects. Run development commands from the root after `uv sync --frozen
  --all-groups` and `pnpm install --frozen-lockfile --ignore-scripts`. Public imports,
  extras and wire contracts are unchanged.

- Public-index release verification also installs the new `otel-httpx` and
  `otel-sqlalchemy` extras and exercises their resource adapters.

## 0.11.0 - 2026-11-01

This release replaces provider-level telemetry declarations with feature-owned
instrumentation and includes the generated Python RPC client fix below.

### Breaking changes

- Remove `fastapi_canon.integrations.opentelemetry.observe` without an alias.
  Replace Provider class declarations with `Feature(instrumentation=[
  instrument(Port, expected=(ExpectedError,)), ...])`, importing `instrument`
  from the same integration. Remove telemetry-only providers/features; spans now
  use the feature declaring their instrumentation as `canon.feature`. For named
  Dishka bindings, pass `component="name"` to `instrument`. Composition creates
  decorators only when telemetry is enabled; the original providers remain
  usable directly in Dishka containers.

### Added

- Instrument asynchronous `__call__` use cases, including inherited methods,
  with the same span, metric, exclusion, exception-privacy and cancellation
  contracts as public async port methods. Span names use `UseCase.__call__`.
- Immutable instrumentation declarations and application-local generated
  decorators allow one feature/provider instance to be reused by enabled and
  disabled applications without sharing instrumentation state.

### Changed

- Expand the README and guides with core HTTP contracts, generated domain SDKs,
  shared problem handling and source error-contract checking examples.
- Workspace TypeScript tooling uses the root-pinned pnpm version and one shared
  `pnpm-lock.yaml`. For source development, use Node.js 24 and run
  `pnpm install --frozen-lockfile --ignore-scripts` at the workspace root; replace
  package-local npm commands with the pnpm commands in the contributing guide.
  Published Python runtime dependencies are unchanged.

### Fixed

- Generated Python RPC clients keep their undocumented error dispatch tables
  private and import problem classes through the existing runtime package facade.
  Regenerate clients to remove Smelt SMT319/SMT320 findings. Declare application
  errors in the service contract; dispatch tables are not consumer extension
  points. Error class exports and wire behavior are unchanged.
- The publication job installs the shared TypeScript tooling before verifying
  public-index installs, so generated-client compilation can run after upload.

## 0.10.0 - 2026-11-01

### Breaking changes

- Regenerated TypeScript HTTP clients return `undefined` for bodyless success
  responses, `null` for JSON null, and a JSON-or-void union for mixed success
  statuses. Regenerate SDKs and remove checks for `null` that meant "no HTTP
  response body"; await bodyless calls normally or check for `undefined`.
  Empty JSON and unexpected bodies for bodyless contracts now raise
  `ProtocolError`; handle that error instead of accepting a silent null value.
- Regenerated Python HTTP clients validate the response schema for the actual
  status, with exact codes taking precedence over `2XX`. Empty JSON, bodies on
  bodyless responses, and payloads matching only another status now raise
  `ProtocolError`. Regenerate SDKs and correct those server responses instead of
  relying on their previous acceptance. Required query/header parameters cannot
  be null; provide a non-null value. Optional null parameters continue to be
  omitted.

- Rename the console command from `canon` to `fastapi-canon`, including all
  transport and check subcommands. Update scripts and CI invocations; the Python
  module entry point remains `python -m fastapi_canon`. Common generation uses
  `fastapi-canon.toml` with `[codegen]`, or `[tool.fastapi-canon.codegen]` in
  `pyproject.toml`. Move version, sources and targets into that codegen section.

- Use the resolved RFC 9457 `type` URI as the discriminator for Canon's compiled
  same-status HTTP problem unions. Regenerate clients and update schema consumers
  that expected `propertyName: code`; `code` remains a stable Canon extension.
  A union with ambiguous type identities, including a domain `about:blank`
  variant combined with a generic problem of the same status, now fails schema
  compilation. Give business-specific problems distinct, stable type URIs.
- `Problem.status` is optional for embeddings and only accepts an integer when
  present. Callers reading it directly must handle `None`; do not pass booleans,
  numeric strings, or floats. HTTP responses still emit their actual status.
- `Problem.as_dict()` preserves explicitly null extension values while omitting
  absent `status`, `detail`, and `instance`. HTTP schemas describe optional
  `detail` and `instance` as strings; omit absent values instead of emitting null.
- Use one Canon 0.10.0 version across the workspace and generated artifact
  metadata.

- Canon streams now send terminal `canon.problem` or `canon.complete` events.
  Declare processing errors with `stream_raises=` and update manual consumers to
  stop on either terminal event. Raw `StreamRouter` behavior is unchanged.
- Regenerated SSE clients raise `ProblemError` for HTTP problem responses and
  terminal stream problems. Catch that class instead of `HttpError` for problem
  bodies. Unexpected EOF without completion is a transport error for Canon
  streams. Regenerate Python and TypeScript clients after upgrading.
- `ProtocolDefinitionError` now shares Canon's `ErrorConfigurationError` base.

### Added

- Shared `Error` definitions in RPC channel and operation `raises=`, with
  `error_registry=` on `RpcChannel`. Canonical domain failures use JSON-RPC
  `error.data.problem`, retaining the numeric JSON-RPC error code and message.
- RPC routes contribute channel catalogs to composition. Export RPC contracts
  with `service.contract(..., app=app)` to use composition's resolved type URIs.
- SSE profile version 1, declared terminal problem schemas, safe processing
  failure handling, generator cleanup, and explicit successful completion.
- Python and TypeScript SDK problem/error runtimes from a common source;
  `ProblemError.problem`, `TransportError`, and `ProtocolError`, plus known RPC
  error subclasses selected by type URI. Unknown problems preserve extensions.
- Generated Python RPC calls/subscriptions validate parameter objects by wire
  aliases, including all-required parameters, so shared DTOs use one mapping.
- Typed RPC method decorators and default-context endpoints preserve normal
  application type inference. The executable API example is checked with mypy.
- An executable example defining one error and registering it with HTTP, SSE,
  and RPC, and a root decision record for future implementation work.

- Version-1 `fastapi-canon generate` configuration and a common operations/model
  plan for OpenAPI, OpenRPC and WebRTC sources, with live export, saved/offline inputs,
  read-only drift checks, explicit identity/name conflict resolution and one
  ownership manifest per SDK output.
- A generated domain facade (`client.projects.get/cancel/events`) with a JSON
  HTTP binding, internal SSE/JSON-RPC/socket bindings, TypeScript WebRTC sessions,
  shared canonical model declarations and one common error runtime per output.
  Connections are lazy; injected clients remain caller-owned.

- Explicit `include-operations` and `exclude-operations` on common codegen
  sources, applied before model planning and binding generation. Adopt part of
  an existing OpenAPI/OpenRPC/WebRTC contract without modifying the export;
  original input hashes and selection settings remain in the SDK manifest.

### Fixed

- Generated TypeScript HTTP clients omit optional null query/header parameters,
  give exact response statuses precedence over `2XX`, and wrap connection
  failures while reading response bodies in `TransportError`, including problem
  responses. Explicit cancellation retains its native abort error.
- Release notes include consecutive older unreleased changelog sections, so
  upgrading from published 0.8.1 to 0.10.0 includes the unpublished 0.9.0 package
  and telemetry migrations below.

- Enforce architecture and test-quality policies with pinned Smelt in the
  workspace gate, pre-commit and CI. Mirror focused HTTP tests, bound async waits
  and compiler subprocesses, and make generated-output and async outcomes explicit.

- Generate Python validation for valid string patterns using lookarounds or
  backreferences, including decimal-string schemas from earlier Pydantic
  exporters. Compiled Python patterns preserve validation in fields and aliases.
- Preserve literal discriminator fields through nested union aliases, including
  variants with several tag values, so generated Python SDKs import and validate.

To adopt the common workflow, configure `codegen.sources`/`codegen.targets` in
`fastapi-canon.toml` and choose a fresh SDK output. Specialized generator commands and their manifests
remain available. See `docs/problems.md` and the root codegen guide.

## 0.9.0 - unreleased

### Breaking changes

- Consolidate SSE, WebSocket RPC/sockets and WebRTC into the `fastapi-canon`
  distribution. Replace dependencies on `fastapi-streams`, `fastapi-rpckit` and
  `fastapi-webrtc` with Canon's `sse`, `websockets` and `webrtc` extras. Replace
  `fastapi-canon[streams]` with `fastapi-canon[sse]`. All transport source and
  generator resources ship in the same wheel; extras select dependencies.
- Migrate imports to `fastapi_canon.integrations.sse`,
  `fastapi_canon.integrations.websockets` and `fastapi_canon.integrations.webrtc`.
  Their public exports include `CanonStreamRouter` and `CanonWebRtcRouter`.
  Update short `fastapi_canon.sse`, `.websockets` or `.webrtc` paths from earlier
  prepared snapshots. Replace the published `fastapi_canon.integrations.streams`
  path with `fastapi_canon.integrations.sse`. WebSocket framework adapters now
  live under `fastapi_canon.integrations.websockets.adapters`; update former
  `fastapi_rpckit.integrations` imports to that prefix. The former top-level
  modules and console scripts are removed. Use `fastapi-canon sse`,
  `fastapi-canon websockets` and `fastapi-canon webrtc` for generator commands.
- Regenerate clients: generator metadata now uses the unified Canon version. Existing
  generated layouts, contract formats and transport behavior are retained,
  including JSON-RPC 2.0 and `fastapi-webrtc.v1`. Socket binding entry points move
  to `fastapi_canon.integrations.websockets.socket_bindings`; update binding
  package metadata.
  See the [package migration guide](docs/package-migration.md) for commands.
- Extend the public `Composition` constructor with `telemetry=`. Update wrappers
  or signature snapshots that mirror this constructor. Existing compositions
  without telemetry keep their behavior. To opt in, install `fastapi-canon[otel]`
  and pass `Telemetry(...)`; register `observe(Port)` as a Dishka Provider class
  attribute beside the binding it decorates. `Feature` requires no new metadata.

### Added

- Return unified development and future releases to `mathisarends/fastapi_canon`
  with the complete imported Git history. Update package links and publisher
  configuration; preserve historical release evidence in its original repository.
- One `fastapi-canon` command and `python -m fastapi_canon` entry point, including
  `fastapi-canon check errors`. Root CLI help loads no transport generators.
- Optional `codegen`, `aiortc` and `otel` dependencies in Canon.
  Native engine and generation dependencies remain absent from the base install.
- Fresh-wheel checks for the base package, each feature, codegen-only and
  combined installations, including minimum dependency profiles.
- Optional, exporter-free OpenTelemetry integration using FastAPI >=0.143 native
  telemetry and application-owned providers. Core retains FastAPI >=0.115.
- Dishka decorators for public async port methods, expected-exception events,
  optional port-duration histograms, and scope/caching/finalizer preservation.
  Port wrappers capture no argument values or exception messages/stack traces.
- Native HTTP server-span Problem Details enrichment with `canon.problem.code`;
  only 5xx set `error.type` and error status. Document the separate behavior of
  FastAPI's endpoint spans for propagated domain exceptions.
- Explicit WebSocket middleware helper forwarding application providers, isolated
  `SpanCapture` testing helpers, reference endpoint trace contracts, and an opt-in
  two-service OTLP demo. See the [telemetry guide](docs/telemetry.md).

## 0.8.1 - 2026-10-25

[GitHub release and artifacts](https://github.com/mathisarends/canon/releases/tag/fastapi-canon-v0.8.1)
and [PyPI](https://pypi.org/project/fastapi-canon/0.8.1/).

- Update the optional Streams integration and development pin to 0.5.0 for the
  TypeScript `unknownEvents` option, `--diff` drift output, the config-driven
  `fastapi-streams sync` command and faster SSE line splitting in generated
  runtimes. Canon's public API and composition behavior remain unchanged; core
  imports continue to work without Streams, RPCkit or WebRTC.
- Streams 0.5.0 changes the connect timeout of SDK-owned generated Python HTTP
  clients. Regenerate stream SDKs and follow the
  [Streams 0.5.0 upgrade notes](docs/history/fastapi-streams.md) if a client
  relied on an unbounded connect time.

## 0.8.0 - 2026-10-25

[GitHub release and artifacts](https://github.com/mathisarends/canon/releases/tag/fastapi-canon-v0.8.0)
and [PyPI](https://pypi.org/project/fastapi-canon/0.8.0/).

### Breaking changes

- Extend the public constructor signatures with `CanonRouter(policy=...)` and
  `Composition(router_policy=...)`. Update wrappers or signature snapshots that
  mirror those constructors. Existing calls without policies keep their behavior.
  To migrate a shared router helper, create a `RouterPolicy` containing its
  `error_registry`, `dependencies`, and shared `raises`, then instantiate
  `CanonRouter(policy=...)` explicitly. Keep `route_class=DishkaRoute` on the
  declaring router. Use `Composition(router_policy=...)` to extend contracts
  across included feature routers; ordinary parent `raises=` remains local.

### Added

- Export immutable `RouterPolicy` for reusable HTTP prerequisites and their error
  contracts. Combine policy, router and operation declarations with the existing
  registry collision checks and identity-based error deduplication.
- Apply composition policies to nested HTTP/SSE operations without mutating
  reusable feature routers or walking FastAPI's internal route tree. Preserve
  FastAPI dependency behavior, combine same-status Problem schemas, and reject
  manual response collisions before installing the composition.
- Optional `python -m fastapi_canon.check_error_contracts` source checker for
  explicit application exceptions missing from direct CanonRouter error
  contracts. It follows typed services, helpers and FastAPI dependencies,
  accounts for supported exception handling and inheritance, and reports
  unresolved paths as incomplete analysis. Warning output is the default;
  strict CI modes and JSON reports are opt-in.
- Reuse source-checker function summaries and inheritance results within each
  invocation, preserving error paths and incomplete-analysis diagnostics. Add a
  reproducible benchmark for source-processing and full-command timings.

### Fixed

- Mark annotated module reassignments as incomplete source analysis, preventing
  later declarations from hiding a missing route error contract or creating a
  false gap for an already registered contract.
- Report expanded `ErrorRegistry(**config)` arguments as incomplete instead of
  treating an unresolved catalog as empty and reporting missing error mappings.

## 0.7.0 - 2026-10-24

[GitHub release and artifacts](https://github.com/mathisarends/canon/releases/tag/fastapi-canon-v0.7.0)
and [PyPI](https://pypi.org/project/fastapi-canon/0.7.0/).

### Breaking changes

- Pin the optional `fastapi-canon[webrtc]` integration to WebRTC 0.3.0. Custom
  engines must use its public `StrEnum` types and keyword-only engine/channel
  options; follow the
  [WebRTC migration notes](docs/history/fastapi-webrtc.md#030---2026-10-24).
  Canon core and the Streams integration keep their existing behavior.

## 0.6.0 - 2026-10-20

### Breaking changes

- Add the optional `fastapi-canon[webrtc]` integration against WebRTC 0.2.0.
  Use `fastapi_canon.integrations.webrtc.CanonWebRtcRouter` with the existing
  `error_registry` and router/session `raises` declarations. Include failures
  from dependencies explicitly. Existing plain `WebRtcRouter` use remains valid.

### Added

- Export registered admission Problem Details in the WebRTC contract and generated
  session descriptor, using the composed registry's type URIs and exact Error
  objects. `raises` documents failures without restricting runtime mapping.
- Validate declarations against the existing registry and map dependency/factory
  failures before peer allocation. Export with `build_contract(app)` after
  composition to use the application's problem type URIs. Follow the
  [WebRTC admission guide](docs/webrtc.md) when migrating a plain router.

## 0.5.5 - 2026-10-20

- Preserve feature-router lifespans when composing application dependencies or
  feature lifespans. Routers now complete shutdown before feature resources and
  the Dishka application container are closed. Router `on_startup`/`on_shutdown`
  handlers still run exactly once, including when the app has its own lifespan.

## 0.5.4 - 2026-10-18

- Update the optional Streams integration and development pin to 0.4.0 for
  always-present SDK manifest provenance and optional error-decoder recipes.
  Canon's public API and composition behavior remain unchanged.
- Regenerate stream SDKs once and commit the updated provenance manifest before
  running drift checks; follow the
  [Streams 0.4.0 upgrade notes](docs/history/fastapi-streams.md).
- Generate absolute commit-pinned changelog links in published release notes.

## 0.5.3 - 2026-10-18

- Update the optional Streams integration and development pin to 0.3.1 for
  generated Python runtime efficiency and additive diagnostic-report provenance.
  Canon's public API and composition behavior remain unchanged; core imports
  continue to work without Streams or RPCkit.
- Regenerate stream SDKs to adopt runtime improvements; follow the
  [Streams 0.3.1 upgrade notes](docs/history/fastapi-streams.md).

## 0.5.2 - 2026-10-18

- Update the optional Streams integration and development pin to 0.3.0 and align
  installation examples. Canon's composition/runtime API is unchanged.
- Consumers regenerating Python stream SDKs must follow the stricter request
  validation and diagnostic migration in the
  [Streams 0.3.0 changelog](docs/history/fastapi-streams.md).

## 0.5.1 - 2026-10-18

- Source preparation passed workspace CI, independent wheel matrices and the
  package release dry run. See the [release process](docs/releases.md).

- Pin the optional Streams integration and development tooling to 0.2.0.
  Regenerated Python SDKs now enforce supported scalar constraints; follow the
  [Streams upgrade notes](docs/history/fastapi-streams.md). Canon core remains
  independent of Streams and RPCkit.

- Correct Streams integration installation examples to match the pinned dependency.

## 0.5.0 - 2026-10-17

### Added

- Public `HTTPMethod` string enum, re-exported from Python's `http` module for
  explicit method lists on REST and stream endpoints.
- Optional `CanonStreamRouter` in `fastapi_canon.integrations.streams`, combining
  native fastapi-streams GET/POST SSE generators with canon's error registry,
  router-wide and local `raises=`, and RFC 9457 HTTP problems from dependencies.
  `Feature` automatically discovers its registry; full native SSE event schemas
  are retained for Python and TypeScript client generation.
- A `streams` extra pinned to `fastapi-streams==0.1.0`, with
  independent core imports and a separate codegen development group.
- An integration API guide in `docs/streams.md` and an executable streaming
  example with Dishka-injected request services, documenting installation,
  HTTP method selection, client generation, and the boundary between
  pre-response HTTP problems, in-stream business error
  events, and transport failures.
- Workspace integration checks, both client generators, and clean-wheel core
  runtime checks without Streams. Imported upstream workflow files are historical;
  the root workflow owns CI in the monorepo.

### Changed

- Develop in `mathisarends/canon` and point package metadata to its package and
  documentation directories. Publish Streams 0.1.0 before Canon 0.5.0 because
  the optional integration pins that version.
- Consumers moving from Streams 0.0.1 must regenerate SDKs and follow its
  single-value enum and reserved error-alias migrations.

## 0.4.0 - 2026-09-10

### Added

- Added `validate_openapi_contracts(app)` and `Composition.validate(app)` as
  intention-revealing OpenAPI contract validation entry points.
- Added `install_openapi_contracts(app)` for low-level success-only
  applications. `Composition` installs the compiler automatically even when no
  error registry exists.

### Changed

- Renamed the `CanonRouter` registry parameter from `errors=` to
  `error_registry=` to distinguish the available error catalog from the
  router-level and operation-level errors declared through `raises=`.
- `Feature` now collects error registries from its direct `CanonRouter`
  contributions when `errors=` is omitted. Identical registry contributions
  across features are merged once.

### Fixed

- Made `CanonRouter` success responses self-contained so standalone JSON and
  bodyless routes no longer expose internal `x-fastapi-canon-success` metadata
  or retain FastAPI's generated JSON content.

## 0.3.0 - 2026-09-10

### Added

- Added `CanonRouter` as the canonical route declaration API, with composable
  router-level and operation-level `raises=` declarations plus a first-class
  `response=` success contract. The lower-level response compilers remain
  supported for ordinary FastAPI routers and migrations.
- Added `CanonResponse.responses()` so success-only routes can declare and
  compile response contracts without an empty `ErrorRegistry`.
- Added `ResponseConfigurationError` for FastAPI `response_model` declarations
  that conflict with a bodyless `CanonResponse`.

### Changed

- Compile response contracts from generated OpenAPI operations through the
  documented `app.openapi` hook, removing internal route-tree traversal and
  route-list copying during installation preflight.
- Validate application-level operation contracts at OpenAPI generation time.
  Plain `APIRouter` declarations no longer inspect runtime status or response
  models; use `CanonRouter` for explicit declaration checks. Hidden routes are
  outside document-level validation.

- Renamed the public success contract from `Response` to `CanonResponse` to
  avoid collisions with response classes commonly used in FastAPI routers.

### Fixed

- Bodyless non-204 success contracts now suppress FastAPI's generated JSON
  response without requiring a matching `response_class` on the route.

## 0.2.0 - 2026-09-10

### Added

- Required feature names and feature-aware composition diagnostics.
- Composition-level `router_factory` support for shared prefixes, tags,
  dependencies, and custom `APIRouter` subclasses.
- Lazy Dishka provider construction from provider classes and zero-argument
  factories.
- Reusable normalized HTTP exception contracts through
  `ErrorRegistry.responses(http_statuses=[...])`.
- Immutable success response contracts through `Response` and the `json()`,
  `empty()`, `stream()`, `binary()`, and `sse()` constructors.
- Combined success, domain-error, and normalized HTTP documentation through the
  `success=` argument to `ErrorRegistry.responses()`.

### Changed

- OpenAPI compilation replaces FastAPI's generated success media type when a
  `Response` contract is declared.
- Documentation now defines composition ownership of the generated Dishka
  container and safe handling of public Problem Details fields.

### Fixed

- Manual FastAPI response definitions without canon metadata are accepted.
- SSE, PDF, readiness, and other non-problem response contracts remain valid
  when an error registry is installed.
- Domain codes that conflict with generated `http_<status>` discriminator values
  are rejected during response compilation.

## 0.1.0 - 2026-09-10

### Added

- Immutable feature composition for FastAPI routers, Dishka providers, error
  registries, exception handlers, and lifespans.
- RFC 9457 Problem Details rendering shared by runtime handlers and OpenAPI.
- Domain, validation, HTTP exception, response-validation, and unhandled-error
  normalization.
