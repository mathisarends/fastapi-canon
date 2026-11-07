# Changelog

All notable changes to `fastapi-canon` are documented in this file.

## Unreleased

The next release containing the breaking changes below must be 0.16.0.

### TL;DR

- Router contracts follow `include_router()` like dependencies: an including
  router's `raises` and `policy` extend its children, and
  `Composition(router_policy=...)` becomes
  `router_factory=lambda: APIRouter(policy=...)`.
- Declare public errors only with `raises=` across HTTP, SSE, RPC and WebRTC.
  Composition collects their `Error` instances; `error_registry=` is gone and
  `Feature(errors=[...])` lists the few errors no operation declares. SSE uses
  the same declaration for admission and terminal stream failures.
- Register reusable WebSocket admission functions with `hooks=(function, ...)`.
  `Inject[T]` obtains their results by return type or resolves application services
  through DI. The shared `APIRouter.include_channel(...)` forwards these options;
  specialized WebSocket routers remain supported.
- `APIRouter` is the only router applications use. Declare streams with
  `@router.sse(...)` and WebRTC sessions with `@router.webrtc(...)`; the SSE and
  WebRTC routers are internal. Engine, ICE, origin and limit options move to each
  declaration, and `SessionCapacity` shares a session budget across declarations.
- Dishka owns resources. Canon closes the admission REQUEST scope before
  acceptance. Return independently usable context data and put resources needed
  until disconnect in SESSION-scoped providers.

### Added

- `Error` infers `extensions_model` from an `extensions` renderer annotated to
  return a Pydantic model, such as `def fields(error) -> RetryFields`. Callable
  extensions without such an annotation still require `extensions_model=`, and
  an explicit `extensions_model=` keeps precedence.
- `APIRouter.sse()`, `include_channel()` and `webrtc()` declare their forwarded
  options as typed keyword arguments (`StreamOptions`, `ChannelOptions` and
  `SessionOptions` in `fastapi_canon.api_router`). Editors complete them and type
  checkers reject misspelled options at the call site instead of at import time.
  Runtime behavior is unchanged.
- `Inject[T]` resolves application services in ordinary FastAPI dependency
  functions, not only in endpoint parameters. Authentication and precondition
  dependencies no longer need Dishka's `@inject` with `FromDishka[T]`;
  `app.dependency_overrides` keeps addressing the original function, and
  composition preflight reports missing bindings of nested dependencies
  (`canon.providers.E003`) before installation.

- Documentation records the DX decisions on MCP activation, the shared router
  prefix, feature metadata ownership, WebSocket `context=` terminology and the
  preferred generation command, and explains MCP error masking with `ToolError`.
  No runtime behavior or public API changed.
- Source checking resolves bounded direct local router/channel factories without
  executing them. HTTP/SSE/RPC endpoints, static error contracts and helper calls
  use independent lexical scopes and stable factory-qualified names. Dynamic
  captures/setup and unsupported factories remain incomplete.

- Source checking follows supported local sync/async context-manager enter,
  body and exit calls. Literal exit suppression and multiple-manager order are
  modeled; unknown suppression, external/decorated/inherited protocols and
  unsupported bindings retain CAN003 without importing application code.

- Source error checking follows explicit calls in eager list/set/dict
  comprehensions, including iterable/filter/result paths, nested clauses and
  supported async forms. Comprehension bindings stay local. Generator expression
  creation no longer counts its deferred body/filter errors as executed; direct
  iteration follows the body, while unproven consumers retain CAN003.

- `fastapi-canon explain CODE` explains existing source, composition and common
  generation codes with a meaning, example, repair and guide. Text is the default;
  `--format json` returns a versioned object. Unknown/application-defined codes
  exit 2 with a hint. Lookup is offline and imports no application or optional
  runtime; checker ownership and emitted code names remain unchanged.

- Common SDK generation supports `--format json` with a versioned envelope for
  success, drift, source-located validation warnings and preflight errors.
  Operation/source/target metadata is reported where available; `--diff` stays
  read-only and puts its diff in a separate JSON field. App stdout cannot mix
  into JSON reports. The Python `(changed, report)` return and specialized
  generators retain their contracts.

- Application CLI targets share dotted-reference and synchronous zero-argument
  factory loading. `inspect` and `check composition --app` accept app factories
  automatically; `inspect --factory` remains accepted. App instances are never
  called as ASGI functions, and common SDK generation reuses each configured
  factory result across contract formats without entering lifespan.

- WebRTC session factories resolve `Inject[T]` and `FromDishka[T]` automatically
  from the WebSocket SESSION container, including sync/coroutine functions and
  sync/async generator dependencies. FastAPI path/header inputs and pre-accept
  HTTP denials are preserved. Generator teardown runs after session cleanup and
  before SESSION provider cleanup; no HTTP Request is inserted into signaling.

- The default `APIRouter` injection supports typed async/sync callable instances,
  bound methods and `functools.partial`, including module/class future
  annotations and inherited call methods. `Inject`/`FromDishka` parameters stay
  out of public schemas, request cleanup and operation names are preserved, and
  caller-owned objects/functions/partial keywords are not modified.

- `APIRouter`, `CanonRouter` and `CanonStreamRouter` accept `namespace=` for
  automatic `<namespace>.<function_name>` HTTP/SSE operation IDs. Without a
  namespace, omitted IDs use the function name. Explicit IDs and custom FastAPI
  ID generators take precedence. Composition and `fastapi-canon check composition`
  reject collisions with `canon.routing.E002`; SDK generation also rejects
  duplicate IDs before writing output, without adding disambiguating suffixes.
  The bundled `build-canon-backend` skill demonstrates namespace-based HTTP/SSE
  names, stable explicit overrides and CLI collision diagnostics.

- `fastapi-canon check errors` also checks `APIRouter.sse()` streams,
  `APIRouter.webrtc()` sessions and `RpcChannel` methods against their `raises=`
  declarations. Loops are now analyzed, and iterating a generator follows
  its body. JSON reports include each operation's `transport`.

- `fastapi-canon check errors` treats calls into packages outside the scanned
  roots (standard library, Pydantic, SQLAlchemy, FastAPI security objects) as
  library calls that raise no application exceptions, instead of reporting
  `CAN003`. Application callables passed to them and construction hooks of
  library-based classes such as `model_post_init` are still followed; decorated
  hooks such as Pydantic validators keep the operation incomplete.

- `fastapi-canon check errors` resolves abstract Dishka ports statically. Calls on
  an abstract method or `Protocol` member follow the implementations bound by
  `Provider` classes in the scanned roots (`@provide` factories and
  `provide(Impl, provides=Port)`); several bindings contribute the union of their
  errors. Abstract calls on `self` follow concrete subclasses, and generic or
  `ABC` base classes no longer hide inherited methods. Unbound ports still
  produce `CAN003`, now reported as
  `abstract implementation without Dishka binding: <method>`.

- `Composition.override(*providers)` returns a copy whose Dishka providers
  replace existing feature bindings, for example fakes in tests. Overrides that
  provide a type no feature provides fail with `canon.providers.E001`.

- `fastapi_canon.APIRouter` resolves `Inject[T]` and Dishka's `FromDishka[T]`
  parameters of HTTP and SSE endpoints without `route_class=DishkaRoute` or
  `@inject`. Injected parameters stay out of the OpenAPI contract, synchronous
  handlers are supported, and endpoints already decorated with `@inject` or
  routers with an explicit `route_class` keep their behavior. FastAPI dependency
  functions that use `FromDishka[T]` still need `@inject`.

- `McpOptions(auth_factory=..., middleware_factory=...)` builds app-specific
  FastMCP adapters from the borrowed Dishka APP container. Synchronous factories
  are validated during composition preflight before application mutation.

- Public `fastapi_canon.integrations.mcp.McpScope` opens an independent Dishka
  REQUEST scope for native MCP injection and returns its container. Applications
  can retain operation-owned resources through shielded execution without
  importing Canon's implementation helpers.

- `McpOptions` accepts `stateless_http`, `mask_error_details` and root
  `middleware`. Both boolean options default to `None`, preserving native
  FastMCP settings. Middleware wraps mounted feature execution inside Canon's
  dependency scope.

- Root `Inject[T]` is shared by WebSocket and MCP integrations. WebSocket
  context functions support `Inject[T]` and reusable `hooks=(function, ...)`
  registrations keyed by return type. Hooks can combine injected services with
  FastAPI path, header and dependency parameters; their results are available
  to connection handlers without becoming public protocol parameters.
- `fastapi_canon.Scoped[UseCase]` executes an async callable use case in a fresh
  short service scope per invocation. Its call preserves argument and result
  types and finishes provider cleanup before returning. Register wrappers at
  APP scope; retain `ServiceScope` for multiple calls sharing one transaction.
  This API is opt-in and introduces no additional breaking changes: existing
  `ServiceScope` signatures, provider lifetimes, transport injection markers,
  routing defaults and dependency requirements are unchanged. Existing
  applications require no migration to continue using their current APIs.
  See [short use-case calls](docs/database.md#short-use-case-calls).

- The bundled `build-canon-backend` skill explains Canon's components and
  Composition/Dishka wiring through focused references and small examples.
  `fastapi-canon skill install --force` refreshes complete skill trees from the
  installed library, restoring missing resources and removing obsolete ones.
  Upgrade the project's Canon dependency first to obtain a newer bundle.

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
- `Composition.check(app)` provides structured preflight findings and per-check
  execution status without starting resources. `Feature(checks=...)` adds explicit
  synchronous checks over immutable composition descriptions.
- `fastapi_canon.inspect_app(app)` explains installed HTTP operations, feature ownership,
  effective prefixes, provider-source names and dependency/error provenance.
  Look up operations by endpoint symbol with `operation(endpoint)` or by public
  ID with the explicit `operation_by_id(...)` method. It reads the snapshot that
  `build` recorded on the application, so the composition is not passed again.
- `fastapi-canon check composition` and `fastapi-canon inspect` expose the same
  Python APIs in text or versioned JSON, separately from import-free source checks.

### Fixed

- Concurrent Python SDK calls preserve the RPC connection error when shared
  binding initialization fails, and subsequent calls can retry. Regenerate
  Python SDKs to receive the runtime fix.

- HTTP injection and Canon SSE reuse `Annotated[Request, ...]` parameters instead
  of adding a second request parameter that causes missing-argument failures.

- CLI help explains target/factory syntax, default and repeatable target
  selection, JSON/text modes, import/lifespan behavior and command-specific
  exit policies. Source read errors identify the invoked public command.
  Parsing, supported forms, defaults and exit conditions are unchanged.

- `inspect_app(app).operation(events)` retains the declared SSE callable
  through terminal processing and automatic injection wrappers, including sync
  generators and nested prefixes. Explicit user decorators retain their own
  registered identity, and ambiguous registrations still require ID lookup.
  References remain outside exported OpenAPI and JSON snapshots.

- HTTP path collision checks retain converter matching when native telemetry
  wrappers in FastAPI 0.143.0 omit their cached route regex. Repeated composition
  builds and late collision checks reconstruct the regex from the effective
  declared path instead of failing with an AttributeError.

- Telemetry configuration errors name the current core minimum, FastAPI 0.121,
  instead of 0.115.

- Generated Python SSE clients annotate their event adapters explicitly. With
  Pydantic 2.11 through 2.13, strict Pyright inferred `TypeAdapter[Unknown]` for
  annotated event unions; generated runtime behavior is unchanged.

### Development

- CI runs the full Python suite against the lowest direct dependencies the
  declared ranges allow, on Python 3.12, alongside the installed-wheel minimum
  matrix. Development tool floors are raised to versions that pass the suite:
  pytest 9.1, pytest-asyncio 1.4, Ruff 0.15 and Uvicorn 0.53.
- Pull requests run the static gates once and the suite on Python 3.12 and 3.14.
  Documentation-only changes skip the suite; dependency, browser and artifact
  checks run when their inputs change. Pushes to `main`, manual runs and the
  `full-ci` label run the complete matrix. The weekly upstream canary adds
  Python 3.13.

### Breaking changes

- `Composition(router_policy=...)` is removed. A Canon router's `raises`,
  `http_statuses` and `policy` now extend every HTTP operation it includes with
  `include_router()`, like its `dependencies`, so the application router carries
  composition-wide contracts. Migrate
  `Composition(router_policy=P, router_factory=lambda: APIRouter(prefix="/api"))`
  to `Composition(router_factory=lambda: APIRouter(prefix="/api", policy=P))`;
  without a factory use `router_factory=lambda: APIRouter(policy=P)`.
- Parent router `raises=`, `http_statuses=` and `policy=` are no longer local to
  the parent's own operations. Included child operations now document those
  errors in OpenAPI, the composition snapshot and generated clients. Move
  declarations that should stay local onto the parent's routes or a sibling
  router. A child's manual `responses=` entry for an inherited status is
  rejected like a local collision.
- Composition snapshots report errors and dependencies contributed by the
  application router with origin `composition.router_factory`; the
  `composition.router_policy` origin no longer exists. The error-contract checker
  marks operations incomplete (CAN003) when routers are included into a Canon
  router that declares `raises`, `http_statuses` or `policy`.
- `error_registry=` is removed from `APIRouter`, `CanonRouter`,
  `CanonStreamRouter`, `CanonWebRtcRouter`, `RouterPolicy` and `RpcChannel`, and
  `RouterPolicy.error_registry` no longer exists. `raises=` is the only
  declaration on routers, policies, channels and operations. Delete
  `error_registry=...` arguments whose errors are already declared in `raises=`;
  move errors that no operation declares into `Feature(errors=[...])`.
- `Feature(errors=...)` takes a sequence of `Error` instances instead of an
  `ErrorRegistry`, and `Feature.errors` is a tuple of `Error` (empty instead of
  `None`). Migrate `Feature(errors=ErrorRegistry(errors=[a, b]))` to
  `Feature(errors=[a, b])`, `feature.errors.errors` to `feature.errors` and
  `feature.errors.contains(a)` to `a in feature.errors`.
- A problem-type URI prefix can no longer be attached to a router, policy or
  channel through `ErrorRegistry(type_base=...)`. Set it once with
  `Composition(errors=ErrorOptions(type_base=...))`; an explicit `Error.type`
  keeps its own URI. Applications assembled without `Composition` install
  `ErrorRegistry(errors=[...], type_base=...).install(app)`, which stays public in
  `fastapi_canon.error` for that purpose. Standalone SSE streams and RPC channels
  resolve URIs through the installed catalog.
- The error-contract checker's catalog is now every `Error` referenced in a
  `raises=` or `errors=` keyword in the scanned source. A mapping listed only in
  `Feature(errors=[...])` reports `CAN001` for operations that raise it without
  declaring it. `ErrorRegistry(...)` declarations no longer contribute mappings,
  and the `CAN002` message names the application catalog instead of the router
  registry.
- Static `Error(headers={...})` values are now documented in OpenAPI as
  constant string headers without `openapi_headers=`, and `Error.openapi_headers`
  reports these effective definitions. An `openapi_headers=` entry still replaces
  the generated definition for the same header name, compared case-insensitively.
  Two errors with the same status and different static values for one header now
  fail OpenAPI generation as conflicting headers; give them distinct statuses or
  one shared `openapi_headers=` definition. Remove `openapi_headers=` entries that
  only repeated `{"schema": {"type": "string"}}` for a static header.
- `ExceptionHandlerSpec` is removed from `fastapi_canon`. `Feature` takes
  `exception_handlers=` as a mapping from exception class to handler, like
  FastAPI's `exception_handlers=`, and `Feature.exception_handlers` is a
  read-only mapping. Migrate
  `exception_handlers=[ExceptionHandlerSpec(DomainError, handler)]` to
  `exception_handlers={DomainError: handler}`, and iterate
  `feature.exception_handlers.items()` instead of reading `spec.exception` and
  `spec.handler`.
- Supported nested route factories now report contract gaps instead of blanket
  CAN003, and their operation names become `module.factory.<locals>.endpoint`.
  Before adopting 0.16.0, update report-name filters and map/declare newly exposed
  escaping errors. Path and decorator line remain separate report fields; see
  [factory coverage](docs/error-checking.md#direct-local-route-factories).
- `StreamRouter`, `CanonStreamRouter`, `WebRtcRouter` and `CanonWebRtcRouter`
  are no longer exported from `fastapi_canon.integrations.sse` or
  `fastapi_canon.integrations.webrtc`. Declare streams on an `APIRouter`: migrate
  `streams = CanonStreamRouter(prefix="/runs")` with `@streams.stream(path, ...)`
  to `router = APIRouter(prefix="/runs")` with `@router.sse(path, ...)`, and
  `app.include_router(streams)` to including `router` in a `Feature`. A typed SSE
  stream ends with `canon.complete` and reports failures as `canon.problem`, so
  regenerate SSE clients after the migration. Existing plain SSE endpoints can
  still get generated clients from an OpenAPI 3.2 contract
  ([native transports](docs/integrations/sse/native-transports.md)).
- WebRTC sessions are declared with `@router.webrtc(path, ...)` instead of
  `rtc = WebRtcRouter(engine=..., prefix=...)` with `@rtc.session(path, ...)`.
  Router-level `engine=`, `ice_servers=`, `on_event=`, `allowed_origins=` and
  `limits=` move to each `webrtc()` declaration; put the path prefix on the
  `APIRouter`. Shared declaration settings can be module constants, as in
  `examples/webrtc/practice`.
- A router-wide `max_sessions=` and `WebRtcRouter.active_sessions` are replaced by
  `SessionCapacity`. Migrate `WebRtcRouter(max_sessions=50)` to
  `CAPACITY = SessionCapacity(max_sessions=50)` passed as `capacity=CAPACITY` to
  every declaration that shares the budget, and read `CAPACITY.active`. A
  declaration's own `max_sessions=` keeps limiting that declaration.
- `fastapi_canon.integrations.webrtc` no longer re-exports the engine adapter
  types, `AdmissionErrors` or `SessionDeclaration`. Import `Engine`,
  `EngineCapabilities`, `PeerConfig`, `PeerObserver`, `SessionDescription` and the
  other adapter types from `fastapi_canon.integrations.webrtc.engines`. The SSE
  and WebRTC facades no longer export `__version__`; use
  `fastapi_canon.__version__`.
- The `agent_runs` SSE example contract now uses Canon terminal frames and
  problem details. Its regenerated clients raise `ProblemError` for problem
  responses instead of `HttpError`, and consumers decode validation problems from
  `ProblemError.problem`.

- Supported context-manager paths can now expose CAN001/CAN002 gaps previously
  hidden behind unsupported `with`/`async with` statements. Before enabling strict
  checking on 0.16.0, map these escaping errors and declare them in `raises`.
  Keep CAN003 for managers whose suppression/protocol remains unknown; see
  [context-manager coverage](docs/error-checking.md#local-context-managers).

- Source checking can now report CAN001/CAN002 for newly followed comprehension
  paths that previously retained incomplete analysis. Before adopting 0.16.0,
  review new findings and declare their existing Error mappings in `raises`, or
  add missing mappings, before enforcing strict CI. Unresolved protocols and
  consumers still retain CAN003; do not treat those reports as full coverage.
  See [comprehension coverage](docs/error-checking.md#comprehensions-and-deferred-generators).

- Unexpected common-generation failures now omit raw exception contents in
  text/JSON diagnostics. Before adopting 0.16.0, update parsers that depended on
  arbitrary stderr text to use the documented JSON `schema_version`, code and
  location fields. Known validation messages remain readable; source warnings
  report constraint locations without copying schema values. See
  [generation diagnostics](docs/unified-canon-concept.md#json-generation-diagnostics).

- CLI target loading rejects async/generator factories, awaitable/generator
  results and uninspectable or required-argument factory signatures consistently.
  Before adopting 0.16.0, use a synchronous inspectable zero-argument wrapper
  returning an app or materialized contract value. Import/factory errors now
  expose the target, phase/type and hint instead of arbitrary exception contents;
  update output parsers that depended on raw error text. Keep eager runtime
  configuration in lifespan/dependency code. Common SDK `factory = true`
  selection and standalone contract forms remain available. See
  [target loading rules](docs/contracts.md#application-target-rules).

- Composition rejects WebRTC factory marker roots unavailable through APP/SESSION
  scope with `canon.providers.E004`. Before adopting 0.16.0, register missing
  services/components at APP or SESSION scope and move per-message REQUEST work
  into explicit child scopes. External containers and unsupported explicit
  wrappers keep ownership with skipped coverage; nested FastAPI dependencies
  remain outside this check. See [factory preflight](docs/system-checks.md#webrtc-factory-dependency-preflight).

- Non-function endpoint callables now need inspectable, named parameter
  signatures. Opaque, variadic and remaining positional-only forms fail during
  setup with a targeted TypeError. Before adopting 0.16.0, wrap them in a typed
  named-parameter function, or bind positional-only arguments with a partial.
  Endpoint constructors needing injection require a function or callable
  instance. Injection markers in an effective custom signature are now honored;
  register their providers, or remove the marker when the parameter is public
  request data. See [typed callable endpoints](docs/routers.md#typed-callable-endpoints).

- Composition preflight validates HTTP/SSE endpoint `Inject[T]` and
  `FromDishka[T]` roots with `canon.providers.E003`. Before adopting 0.16.0,
  register missing service/component bindings at a scope available to the
  endpoint. HTTP handlers can use APP/SESSION/REQUEST services. Typed SSE bodies
  execute after HTTP REQUEST cleanup, so inject APP services or APP-scoped
  `ServiceScope` factories for short request-scoped work inside the stream.
  Stream bodies that previously failed at iteration, or borrowed closed REQUEST
  resources, are rejected before installation. Custom route classes and external
  containers keep ownership with skipped preflight coverage; review incomplete
  reports before using `--require-complete`. FastAPI dependency functions remain
  outside this endpoint-root check. See [endpoint dependency coverage](docs/system-checks.md#http-and-sse-endpoint-dependency-preflight).

- Composition preflight validates default-resolver JSON-RPC method `Inject[T]`
  roots and rejects missing default-component APP/SESSION/REQUEST bindings with
  `canon.providers.E002`. Before adopting 0.16.0, register each service in feature
  providers at a scope available to the call. Framework connection/path values
  and reachable context-hook results do not require duplicate providers.
  Custom resolvers, resolver scopes, dynamic admission values, external
  containers and unsupported transport roots remain application-owned; their
  skipped checks make preflight reports incomplete rather than failing against
  an unrelated Dishka graph. Assess that coverage before using
  `--require-complete`. Provider bodies and lifespans are never executed by this
  validation. See [RPC dependency coverage](docs/system-checks.md#rpc-method-dependency-preflight).

- Composition preflight rejects duplicate effective WebSocket paths across
  features and existing application routes with `canon.routing.E004`, including
  native WebSockets colliding with JSON-RPC channels and equivalent parameter
  names/converters. Before adopting 0.16.0, give each contribution a distinct
  effective path or remove the duplicate. Changing the endpoint name or channel
  namespace does not change dispatch. HTTP and WebSocket routes can still share
  a URL. See [path validation](docs/system-checks.md#findings-and-coverage).

- Composition now rejects ambiguous HTTP method/path contributions with
  `canon.routing.E003`, including hidden routes, existing application routes and
  paths differing only in parameter names with equivalent converter regexes.
  Before adopting 0.16.0, remove duplicate registrations or give them distinct
  effective paths or HTTP methods. Different operation IDs do not prevent route
  shadowing. Intentional static/parameter overlap remains supported; see
  [HTTP path checks](docs/system-checks.md#findings-and-coverage).

- Provider-factory analysis in `fastapi-canon check errors` now follows local
  reassignments, aliases and conditional return/yield paths in execution order.
  Strict checks can fail for previously missed implementation errors; unmodeled
  factory control flow remains incomplete (`CAN003`). Before adopting 0.16.0,
  declare the actual implementation errors in `raises=` (and map them to `Error`
  values), or simplify opaque factories to explicit supported bindings. Do not
  rely on an earlier assignment or an unreachable return to establish coverage.

- Automatic HTTP/SSE operation IDs now use the endpoint function name, optionally
  prefixed by the declaring router's `namespace`, instead of FastAPI's default
  function/path/method combination. This changes exported OpenAPI and generated
  SDK members and belongs to 0.16.0. Preserve existing public names by copying
  their previous OpenAPI `operationId` into explicit `operation_id=` declarations,
  or keep the legacy naming policy with
  `generate_unique_id_function=fastapi.utils.generate_unique_id` on the router.
  When adopting the new names, update operation mappings and regenerate SDKs.
  Rename colliding functions, use distinct namespaces, or set distinct explicit
  IDs; reused handlers and multiple-method routes no longer receive automatic
  path/method suffixes. Declare separately named operations for separate mounts
  or HTTP methods. See [operation naming](docs/routers.md#automatic-http-and-sse-operation-names).

- `Composition.build(app)` replaces `Composition.apply(app)` and
  `Composition.validate(app)`. It installs the composition, checks the installed
  application including OpenAPI and returns it, so an application can no longer
  start unvalidated. Replace `composition.apply(app)` and any following
  `composition.validate(app)` with `composition.build(app)`; register plain
  FastAPI routes before calling `build`, or call `build` again after adding them.
  `Composition.check(app)` remains the non-raising report. This change belongs to
  the next minor release, 0.16.0.

- SSE removes `stream_raises=` from `CanonStreamRouter` and its stream decorators,
  including `APIRouter.sse()`. Merge previous `raises` and `stream_raises` values
  into one `raises=[...]` declaration. Router-level and operation-level errors now
  describe both HTTP admission responses and terminal `canon.problem` frames;
  the failure phase determines the transport representation. Keep admission
  checks in dependencies because generator bodies execute after response start.
  Regenerate SDKs to include the combined admission and stream problem contracts.
  This change belongs to the next minor release, 0.16.0.
- `raises=` automatically registers canonical `Error` definitions on routers,
  policies, SSE streams, WebRTC admission and RPC channels/operations. Remove
  duplicate registry entries and `error_registry=` arguments used only to repeat
  these declarations. Keep explicit registries for additional undeclared errors,
  and configure a common problem URI prefix with `Composition(errors=ErrorOptions(
  type_base=...))`. Repeated references to the same definition are deduplicated;
  conflicting definitions still fail configuration checks.
- The new MCP factory fields extend `McpOptions` for 0.16.0. Move auth and shared
  middleware adapters that read `app.state.dishka_container` into synchronous
  factories receiving the container directly. Supply either `auth` or
  `auth_factory`; factories must not resolve resources or start tasks during
  composition preflight.

- The MCP facade adds the public `McpScope` API for 0.16.0. Replace custom imports
  of `integrations.mcp.dependencies.request_scope` and `inject_handler` used to
  obtain a scoped container with `async with McpScope(app_container)() as scoped`.
  Normal composed MCP calls retain their existing scope behavior.

- The new MCP option fields extend the public `McpOptions` constructor for
  0.16.0. Existing declarations retain their behavior. To preserve a previous
  custom stateless endpoint when adopting Canon, set
  `McpOptions(stateless_http=True)` explicitly.

- MCP discovery and execution reject duplicate public component identities across
  mounted servers, including collisions produced by namespace concatenation.
  Rename local tools/prompts or their mount namespaces before upgrading to
  0.16.0. Distinct FastMCP versions remain supported. Validation uses the live,
  caller-visible catalog and does not query authenticated providers at startup.

- MCP installation rejects existing application and feature HTTP routes or mounts
  that shadow its endpoint, including router prefixes and catch-all paths. Move
  those routes or select a distinct `McpOptions(path=...)` before upgrading to
  0.16.0. Preflight reports `canon.mcp.E001` before application mutation.

- WebSocket context preparation with `resolver=Dishka()` now owns a short
  REQUEST scope and closes it before acceptance. Return independently usable
  context data; put connection-lifetime resources in SESSION-scoped providers.
  Context `FromDishka[T]` parameters also resolve in this short scope, so code
  retaining admission resources through an accepted connection must migrate.
  Existing `FromDishka[T]` syntax remains supported. Move resource ownership to
  the appropriate Dishka provider and pass immutable IDs or other independent
  values through the context. Generator resources belong in Dishka providers,
  not in `hooks=`. RPC wire formats and generated clients are unchanged.
- `fastapi-canon skill install` now creates both
  `.agents/skills/build-canon-backend` and `.claude/skills/build-canon-backend`.
  Use `--destination .agents/skills/build-canon-backend` to retain the previous
  single-directory behavior. Existing destinations still require explicit
  `--force` to overwrite; keep local customizations outside the managed skill.

- Duplicate HTTP operation IDs now fail composition installation. Assign distinct
  `operation_id` values, including across existing app routes and features.
  Split multi-method routes when they would export the same operation ID twice.
- Composition preflight now compiles HTTP/OpenAPI contracts before installation.
  Fix invalid response/catalog metadata before `apply()`; failures previously
  deferred to OpenAPI generation can now occur during application construction.
- `Composition.validate(app)` also runs registered feature checks. Keep checks
  deterministic and free of startup work; migrate configuration diagnostics to
  the structured `check(app)` report. Configuration failures remain subclasses of
  `FeatureConfigurationError` and include finding codes and remediation hints.

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
- Error installation rejects domain exception classes and codes reserved by the
  enabled normalizers (`request_validation_error` and `internal_server_error`).
  Rename domain codes, use application-specific exception classes, or disable the
  relevant normalizer. Normalization flags must be booleans, and `ErrorOptions`
  now validates registry names and absolute, query-free, fragment-free type bases
  at construction instead of accepting invalid values until installation.
- The core now requires `dishka>=1.10,<1.11`, and the `aiortc` extra requires
  `aiortc>=1.14,<1.16`. Composition reads Dishka's container registry and
  injection helpers, and the aiortc engine preserves negotiated codecs through
  transceiver internals; neither upstream guarantees them across minor releases.
  Before adopting 0.16.0, keep Dishka on 1.10.x and aiortc on 1.14.x or 1.15.x.
  Later Canon releases widen these ranges after the upstream canary passes. See
  [upstream internals](docs/compatibility.md#upstream-internals).

- The `fastapi_canon` root exports only the names needed to build an
  application: `APIRouter`, `CanonResponse`, `Composition`, `Error`,
  `ErrorOptions`, `ExceptionHandlerSpec`, `Feature`, `Inject`,
  `ResponseHeaders`, `RouterPolicy`, `Scoped`, `ServiceScope`,
  `export_contracts` and `__version__`. The root no longer resolves optional
  integration names lazily. Before adopting 0.16.0, update imports of the
  removed names:

  | Removed root import | Replacement |
  | --- | --- |
  | `RpcChannel` | `fastapi_canon.integrations.websockets.RpcChannel` |
  | `Database`, `AlembicMigrations`, `TransactionLocks`, `run_alembic_environment` | `fastapi_canon.integrations.database` |
  | `ErrorRegistry`, `Problem`, `ErrorConfigurationError` | `fastapi_canon.error` |
  | `ConfigurationError`, `ErrorConfigurationError`, `FeatureConfigurationError`, `ResponseConfigurationError` | `fastapi_canon.exceptions` |
  | `CheckContext`, `CheckIssue`, `CheckReport`, `CheckResult`, `FeatureCheck`, `CompositionInfo`, `DependencyInfo`, `ErrorInfo`, `FeatureInfo`, `OperationInfo` | `fastapi_canon.diagnostics` |
  | `install_openapi_contracts`, `validate_openapi_contracts` | `fastapi_canon.openapi` |
  | `Contracts` | `fastapi_canon.contracts` |
  | `HTTPMethod` | `http.HTTPMethod` from the standard library |
  | `CanonRouter` | `fastapi_canon.APIRouter`, which accepts the same router options |
  | `ProviderFactory`, `ProviderSource`, `RouterFactory` | No replacement; annotate with `Provider \| Callable[[], Provider]` or `Callable[[], fastapi.APIRouter]` |

  All configuration errors are now defined in `fastapi_canon.exceptions`; class
  identity and the `ValueError` base are unchanged, so existing `except` clauses
  keep working once their imports are updated. Projects initialized with
  `AlembicMigrations` have a generated `migrations/env.py` that imports
  `run_alembic_environment` from the root; change that line to
  `from fastapi_canon.integrations.database import run_alembic_environment`.
  Modules other than the root, `error`, `diagnostics`, `exceptions`, `openapi`,
  `contracts` and the documented `integrations` facades are internal; see
  [public namespaces](docs/canon.md#public-namespaces).

### Fixed

- `fastapi-canon check errors` analyzes operations declared on the shared
  `APIRouter` like those on `CanonRouter`, instead of reporting them as
  unsupported router declarations.
- WebSocket routes without an explicit resolver use Composition's Dishka
  container for injected services, including context hooks. Explicit resolvers
  keep precedence and provider scope cleanup is preserved.
- `fastapi-canon check errors --help` shows the error check's own options, and
  missing arguments report the public command name. `check` uses real
  subcommands for errors and composition.
- The concepts example passes `features=[...]` to `Composition`; the README's
  supported Python range renders correctly.
- SSE sync configuration uses `[tool.fastapi-canon.sse]` and
  `[[tool.fastapi-canon.sse.clients]]`. The old `tool.fastapi-streams` tables
  remain accepted for one transition release with a visible deprecation warning;
  rename them to the Canon tables, which take precedence when both exist.
  Regenerate SSE SDKs to migrate `.fastapi-streams.json` to
  `.fastapi-canon-sse.json`; old ownership is retained for cleanup, handwritten
  files are preserved, and check mode reports migration without writes.
- Generated TypeScript SDKs resolve endpoint wire names independently of their
  camelCase helper exports, including automatically named channel endpoints and
  explicit names such as `project_session`. Regenerate SDKs to pick up the fix.
- Handler conflicts are checked before error installation changes the app.
  Domain errors on ordinary WebSockets use an HTTP problem denial before accept
  when the server supports it; otherwise they close with 1008 (client failures)
  or 1011 (server failures), preserving the problem code as the close reason.
  RPC method failures retain their JSON-RPC problem envelopes.
- Composition validates contracts before opening lifespan resources and keeps
  the dependency container alive through feature and router cleanup.

### Migration

- Adopting `Scoped` is optional. For a single coroutine use-case call, replace
  `async with service_scope() as use_case: result = await use_case(...)` with
  `result = await scoped_use_case(...)`, after registering an APP-scoped
  `Scoped(container, UseCase)` provider. The constructor requires the APP
  container and a class with an asynchronous coroutine `__call__`; it rejects
  request/socket child containers, synchronous callables and async generators.
  Keep `ServiceScope` when several calls must share one scope/transaction, or
  when the service exposes ordinary methods instead of `__call__`. Each
  `Scoped` invocation has its own scope and, with Canon's Database providers,
  commits independently; splitting a previously shared transaction changes
  atomicity and is an application decision. Return materialized values rather
  than scoped resources or lazy ORM graphs. Commit/cleanup errors propagate
  before a result reaches the caller; no automatic retries, read-only mode or
  consistent-snapshot guarantee is added. Continue using the existing transport
  injection API (for example `FromDishka[Scoped[UseCase]]` for HTTP). `Scoped`
  itself does not change transport injection; the separate WebSocket/MCP
  `Inject[T]` additions and admission changes are described above.

- Use `from fastapi_canon import APIRouter` and
  `from fastapi_canon.integrations.websockets import RpcChannel` for a feature's shared
  HTTP, SSE, RPC and WebRTC declarations. Mount reusable channel groups with
  `router.include_channel(channel, path=...)`; the mount captures a snapshot, so
  later changes to the source group do not alter existing endpoints. Grouped
  methods share the mounted socket and its path context. For different project
  IDs, create separately bound SDK clients rather than passing an HTTP path
  argument to retarget an existing RPC connection. See [routers](docs/routers.md).
- If an application owns its framework exception handlers, set
  `ErrorOptions(include_validation_error=False, include_http_exceptions=False,
  include_unhandled_error=False)` on the composition. Otherwise remove conflicting
  handlers and let the composed catalog provide the normalized problem responses.
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
  [Streams 0.5.0 upgrade notes](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-streams.md) if a client
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
  [WebRTC migration notes](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-webrtc.md#030---2026-10-24).
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
  [Streams 0.4.0 upgrade notes](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-streams.md).
- Generate absolute commit-pinned changelog links in published release notes.

## 0.5.3 - 2026-10-18

- Update the optional Streams integration and development pin to 0.3.1 for
  generated Python runtime efficiency and additive diagnostic-report provenance.
  Canon's public API and composition behavior remain unchanged; core imports
  continue to work without Streams or RPCkit.
- Regenerate stream SDKs to adopt runtime improvements; follow the
  [Streams 0.3.1 upgrade notes](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-streams.md).

## 0.5.2 - 2026-10-18

- Update the optional Streams integration and development pin to 0.3.0 and align
  installation examples. Canon's composition/runtime API is unchanged.
- Consumers regenerating Python stream SDKs must follow the stricter request
  validation and diagnostic migration in the
  [Streams 0.3.0 changelog](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-streams.md).

## 0.5.1 - 2026-10-18

- Source preparation passed workspace CI, independent wheel matrices and the
  package release dry run. See the [release process](docs/releases.md).

- Pin the optional Streams integration and development tooling to 0.2.0.
  Regenerated Python SDKs now enforce supported scalar constraints; follow the
  [Streams upgrade notes](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-streams.md). Canon core remains
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
