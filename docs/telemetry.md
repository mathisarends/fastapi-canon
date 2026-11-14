# Optional OpenTelemetry integration

Canon adds port spans to FastAPI's native HTTP telemetry. Endpoints, application
services and domain classes need no telemetry annotations or imports.

Install `fastapi-canon[otel]`. This extra adds the OpenTelemetry API and
instrumentation helpers; the SDK and exporters remain application dependencies.
Canon core, with or without telemetry, requires FastAPI >=0.143.1.
Recent FastAPI versions themselves depend on the OpenTelemetry API.

## Developer-facing API

Declare instrumentation on the feature that owns the application boundary:

```python
from dishka import Provider, Scope, provide
from fastapi import FastAPI
from fastapi_canon import Composition, Feature
from fastapi_canon.integrations.opentelemetry import Telemetry, instrument


class TaskProvider(Provider):
    repository = provide(
        SqlTaskRepository,
        provides=TaskRepository,
        scope=Scope.REQUEST,
    )
    service = provide(TaskService, scope=Scope.REQUEST)


tasks = Feature(
    name="tasks",
    routers=(router,),
    providers=(TaskProvider,),
    integrations=[
        instrument(TaskRepository),
        instrument(TaskService, expected=(TaskNotFound,)),
    ],
)
app = Composition(
    features=(tasks,),
    integrations=[
        Telemetry(
            tracer_provider=tracer_provider,
            exclude=lambda scope: scope["path"] in {"/health", "/ready"},
        ),
    ],
).build(FastAPI())
```

`instrument(Port, OtherPort, ...)` requires at least one positional port and
returns one immutable `Instrumentation` part, which goes into
`Feature(integrations=[...])` like any other integration part. The keyword-only
`expected` and `component` options apply to every port in the call. Use one call
per group of ports with different settings:

```python
integrations = [
    instrument(TaskRepository, AuditRepository),
    instrument(TaskService, expected=(TaskNotFound,)),
    instrument(BillingGateway, component="billing"),
]
```

Dishka providers stay focused on their normal bindings.
Existing `@provide` factory methods work too. Instrumentation requires
`Telemetry` in `Composition(integrations=[...])`; a feature that instruments ports
in a composition without it is rejected with `canon.integrations.E001`. Use
`Telemetry.disabled()` to keep the declarations without producing telemetry; it
adds no proxies or spans. Direct Dishka containers can use the same
original providers without Canon. Features and provider instances can be reused
across applications; their instrumentation runtime belongs to each composition.

Every public coroutine method and asynchronous `__call__` declared on the port,
including inherited methods, gets an `INTERNAL` span. For example,
`instrument(GetActiveUser)` observes `await get_active_user(user_id)` with a
`GetActiveUser.__call__` span. Synchronous methods, private methods and async
generators are passed through. At least one public coroutine method or async
`__call__` is required. There are
no method-name strings, parameter mappings, or implicit argument capture. For
custom attributes, use an explicit application-owned adapter and ordinary
OpenTelemetry calls.

Dishka still owns scope, caching and finalization of the original dependency.
The integration caches a proxy class and method wrappers per registration and
composition, then creates one proxy per dependency resolution. It forwards method
calls and public attribute reads. Its `__class__` reports the wrapped adapter's
actual type, so `isinstance(dependency, Port)` and Pydantic arbitrary-type instance
validation accept an adapter implementing that class. The model retains the proxy
and subsequent calls still produce telemetry. An unrelated adapter remains invalid,
even if its methods match the port or its provider declares `provides=Port`.
`type(dependency)` still returns the proxy class; identity, exact `type(...) is ...`
checks, attribute mutation and other Python special methods are not forwarded.
Register a port only once per Dishka component, including across
features. Use `instrument(Port, component="billing")` for a named binding; the
default is Dishka's empty component. Spans belong to the feature declaring the
instrumentation, even when another feature contributes the binding.

Pydantic's `Field(exclude=True)` controls serialization, not validation. An internal
service reference can remain concretely typed with
`model_config = ConfigDict(arbitrary_types_allowed=True)` and
`registry: ToolAccessRegistry = Field(exclude=True)` when the wrapped adapter is a
`ToolAccessRegistry`. This also applies to FastMCP `AccessToken` subclasses returned
by an `Mcp(auth_factory=...)` verifier. Token validity, tool/turn scoping and
revocation remain the application's responsibility. Keep request-scoped services
out of longer-lived tokens; return detached identity data for those bindings.

### Migration from 0.11

`instrument(Port)` now returns a tuple rather than a single declaration.
Replace `Feature(instrumentation=[instrument(Port)])` with
`Feature(instrumentation=instrument(Port))`. Combine ports sharing settings with
`instrument(FirstPort, SecondPort, expected=(ExpectedError,))`. Unpack each call
with `instrument(...)` when combining groups with different settings in a list
or tuple. Replace `instrument(port=Port)` with the positional `instrument(Port)`.

### Migration from 0.10

`observe()` has been removed, without an alias. Move its declarations out of
Provider classes into `Feature(instrumentation=instrument(Port, OtherPort))`. Remove
separate telemetry-only providers/features. Keep `expected=(...)` on the new
declaration, and supply `component=` explicitly for named Dishka bindings.
SDK configuration and ownership remain unchanged.

## Configuration and ownership

`Telemetry` is immutable and keyword-only:

| Option | Default | Meaning |
| --- | --- | --- |
| `tracer_provider`, `meter_provider`, `logger_provider` | `None` | Use the current global provider; explicit providers are local to this application |
| `tracing`, `metrics`, `logs` | `True` | Enable FastAPI's native signals; `tracing` also gates port spans |
| `operation_spans` | `True` | FastAPI dependencies, endpoint, serialization and background-task spans |
| `port_spans` | `True` | Instrument declared coroutine methods and async `__call__` |
| `port_metrics` | `False` | Record `canon.port.duration` in seconds, independently of port spans |
| `problem_details` | `True` | Enrich the native HTTP server span with handled Problem Details outcomes |
| `exclude` | `None` | ASGI scope predicate; suppress native telemetry and ports, and participating downstream instrumentation for HTTP requests |

Apply composition before startup. Canon forces FastAPI's `auto_configure=False`,
including when the environment requests automatic exporters. It never creates
exporters, sets global providers, or flushes/shuts down application providers.
The application may install global providers in its lifespan before requests
start; both FastAPI and the port tracer resolve them lazily. Explicit providers
should be created before composition and shut down by an application lifespan.
Without a Canon telemetry option, FastAPI keeps its own constructor/environment
configuration; `telemetry=None` does not disable an independently configured
FastAPI pipeline.

### HTTP exclusion and suppression

Canon evaluates `exclude` once per ASGI scope. For an excluded HTTP request it
establishes OpenTelemetry instrumentation suppression for the downstream
application call, including instrumented services, ports, HTTP clients and other
instrumentations that honor that context. No additional application suppression
middleware is needed. Participating spans and metrics are suppressed; manual
spans, application logs and independently collected metrics are not automatically
filtered. Sampling is separate from exclusion.

Inherited OpenTelemetry suppression also disables native HTTP telemetry and
Canon ports, even when `exclude` returns `False`. HTTP-only suppression disables
native HTTP and participating HTTP client instrumentation while leaving Canon
port instrumentation enabled. Nested Canon HTTP applications cannot re-enable
an inherited exclusion. Suppression is restored on completion, exceptions and
cancellation, and concurrent requests keep independent execution contexts.

These downstream suppression guarantees apply to HTTP requests. WebSocket
exclusion still suppresses native telemetry and Canon ports; RPC-specific
configuration remains explicit as described below.

### Injecting configuration into infrastructure

Each Composition-owned Dishka container binds the same immutable `Telemetry`
instance at `Scope.APP`, in every contributed Dishka component. Infrastructure
providers can depend directly on `Telemetry`. Composition reserves that binding;
remove custom bindings of the same type rather than overriding it.

With `Composition(features=features, telemetry=None)`, infrastructure receives
`Telemetry.disabled()`: tracing, metrics and logs are off, and the adapters below
leave resources untouched without importing their optional runtimes. This does
not disable an independently configured FastAPI pipeline. Without contributed
providers Composition still avoids creating an otherwise unused DI container.

Standalone containers explicitly supply the configuration:

```python
container = make_async_container(Infrastructure(), options.provider())
# Or, for uninstrumented standalone use:
container = make_async_container(Infrastructure(), Telemetry.disabled().provider())
```

`options.provider(component="billing")` selects a named Dishka component. These
providers return the borrowed configuration; they never create or close SDKs.

### Explicit resource adapters

Install `fastapi-canon[otel-httpx]` plus the HTTPX or HTTPX2 client library you use,
and `fastapi-canon[otel-sqlalchemy]` plus your database driver. The SQLAlchemy extra
includes its asyncio dependencies, including greenlet, for sync/async engine
support across supported SQLAlchemy versions. The core `otel`
extra does not install client libraries, SQLAlchemy or the SDK.

```python
from collections.abc import AsyncIterator

import httpx2
from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fastapi_canon.integrations.opentelemetry import Telemetry
from fastapi_canon.integrations.opentelemetry.httpx import instrument_httpx
from fastapi_canon.integrations.opentelemetry.sqlalchemy import instrument_sqlalchemy


class Infrastructure(Provider):
    @provide(scope=Scope.APP)
    async def client(self, telemetry: Telemetry) -> AsyncIterator[httpx2.AsyncClient]:
        async with httpx2.AsyncClient(timeout=10) as client:
            with instrument_httpx(client, telemetry):
                yield client

    @provide(scope=Scope.APP)
    async def engine(self, telemetry: Telemetry) -> AsyncIterator[AsyncEngine]:
        engine = create_async_engine("sqlite+aiosqlite:///application.db")
        try:
            with instrument_sqlalchemy(engine, telemetry, pool_name="primary"):
                yield engine
        finally:
            await engine.dispose()
```

Both context managers also support synchronous resources and return the original
instance. Their scope must outlive in-flight operations. They detach on normal
exit, failure and cancellation, without closing resources or borrowed providers.
Tracing and metrics follow their respective application switches independently.
Both-off configurations are no-ops, including without optional packages installed.

For Canon-owned database engines, pass a reusable declaration to the engine
context hook:

```python
from fastapi_canon.integrations.database import Database
from fastapi_canon.integrations.opentelemetry.sqlalchemy import (
    SqlAlchemyInstrumentation,
)

database = Database(
    url=settings.database_url,
    engine_context=SqlAlchemyInstrumentation(
        telemetry=telemetry,
        pool_name="backend",
    ),
)
```

`SqlAlchemyInstrumentation` is immutable and accepts the same telemetry options
as `instrument_sqlalchemy`: `pool_name`, `pool_metrics` and `capture_statement`.
It creates a fresh context for each engine. Canon detaches the instrumentation
before disposing its pool, including failed startup and cancellation. Custom
providers can also use `with instrumentation(engine):` with a configured
declaration. Engines and SDK providers remain owned by their original owner.

HTTPX uses contrib's public `instrument_client` / `uninstrument_client` API for
the supplied client only. Already instrumented clients are rejected so another
owner's instrumentation is not removed. Do not modify or independently instrument
the same client during the scope. Contrib's client ownership marker is the only
private client state consulted; integration tests cover rejection and cleanup.

The adapter's HTTP client spans retain method, server address/port, status,
protocol and error type.
They omit URL/path/query, headers, payloads, exception messages and stack traces,
including header capture requested through contrib environment variables.
`capture_url=True` explicitly permits upstream URL attributes, including query
strings; apply application redaction if necessary. HTTP metrics use contrib's
standard duration instruments and labels. No custom request/response hook runs.

SQLAlchemy uses listeners attached to that engine instance, including
`AsyncEngine.sync_engine`, rather than the global contrib instrumentor. It never
wraps engine factories or removes foreign listeners. Query spans are named
`DB query`; spans and `db.client.operation.duration` metrics contain only the
database system and, for failures, exception type. `capture_statement=True`
allows SQL text on spans, which may contain literals. Bound parameters, database
URLs, exception messages and SQL comments are never added by Canon.

When metrics are enabled, `db.client.connection.count` observes used and idle
connections independently of request suppression. Queue pools use their public
snapshot methods; pools without those methods report connections observed since
instrumentation began. Give each engine sharing a meter a unique `pool_name`.
`pool_metrics=False` omits these observations. Detaching an engine stops only its
pool observations. The callback registry holds weak references and does not own
engines or SDK providers. Instrumenting a resource twice concurrently is rejected.

FastAPI currently accepts telemetry configuration only in its constructor.
Canon bridges that boundary by updating the existing `_telemetry` dictionary,
guarded by a FastAPI >=0.143 check and integration tests. When problem enrichment
is enabled, a local, non-owning tracer-provider adapter preserves problem codes
when FastAPI finalizes a 5xx span. It does not replace the global provider.

### Planned follow-up: managed Console SDK preset

An explicitly selected `Telemetry.console(...)` preset is planned, but is not
implemented in this API. It should own application-local SDK providers, exporters
and cleanup, including partial initialization and startup failures. SDK shutdown
must follow feature and DI cleanup. Disabled presets must allocate no SDK
resources; externally supplied providers remain borrowed. Configuration must be
reusable across applications, with separate owned resources per application and
no changes to global providers. Custom SDK configuration remains supported.

## Span and error contracts

Port spans use `{Port}.{method}`, such as `TaskRepository.get_by_id`, with:

- `code.function.name`: the same port/method name;
- `canon.feature`: the feature declaring the instrumentation;
- `canon.port`: the port class name;
- `canon.adapter`: the resolved implementation class name.

An unexpected exception is re-raised, adds an `exception` event containing only
`exception.type`, and sets `error.type` to the exception class name and status to
`ERROR`. Exception messages, stack traces, arguments, return values, payloads,
headers, and SQL parameters are not captured by the port wrapper.

`instrument(Port, expected=(TaskNotFound,))` instead records a
`canon.port.expected_exception` event with `exception.type`, leaves span status
unset, and omits `error.type`. Subclasses match too. This is an application
classification, not a universal OpenTelemetry rule. Cancellation propagates
without an error event/status. These settings apply to every port in the call;
other groups retain their own expected-failure classification when the same
exception crosses them.

The optional histogram uses only `canon.feature`, `canon.port`,
`code.function.name`, and, for unexpected failures, `error.type`. Argument values
and adapter names never become metric labels.

For handled Problem Details, the native server span gets `canon.problem.code`
from the stable response code. Only 5xx also set `error.type` to that code and
status to `ERROR`. A handled 4xx leaves server span status unset and does not add
`error.type`, following the [HTTP conventions](https://opentelemetry.io/docs/specs/semconv/http/http-spans/).
This includes domain errors, validation, HTTP exceptions, rendering failures,
and unhandled exceptions normalized by Canon's error handlers.

FastAPI's native `fastapi.endpoint` span can still mark a propagated domain
exception as `ERROR`, even if Canon later turns it into an expected 404. Canon
does not rewrite FastAPI's operation spans. Use the HTTP server status and port
classification when defining alerts; set `operation_spans=False` if only server
and application-boundary spans are wanted. See also the
[general error conventions](https://opentelemetry.io/docs/specs/semconv/general/recording-errors/).

## Third-party instrumentation and RPC

Library instrumentation belongs to the application provider that owns the engine
or client. The [reference demo](../examples/reference-service/README.md#local-telemetry-demo)
uses the explicit resource adapters above and injected configuration to propagate
W3C context through an HTTPX2 client and add SQL query spans below repository spans.

HTTPX2 supports `HTTPX2ClientInstrumentor.instrument_client(client, ...)` and
`uninstrument_client(client)`. SQLAlchemy's documented
`SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, ...)` also wraps
engine factories process-wide, and `uninstrument()` removes its instrumentation
globally. Those semantics belong to the direct contrib API, not Canon's
engine-local adapter. Do not combine them on the same engine.

WebSocket controller tracing is configured explicitly when a channel is mounted:

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature
from fastapi_canon.integrations.opentelemetry import Telemetry

telemetry = Telemetry(tracer_provider=tracer_provider, meter_provider=meter_provider)
router = APIRouter()
router.include_channel(
    browser_channel,
    path="/ws",
    middleware=(telemetry.websocket_middleware(),),
)
app = Composition(
    features=(Feature(name="browser", routers=[router]),),
    telemetry=telemetry,
).build(FastAPI())
```

Select `fastapi-canon[websockets,otel]` to enable the bundled middleware. The
helper forwards providers and the `tracing`/`metrics` switches. It keeps the
default `connection_spans=False` and leaves remote baggage propagation disabled.
Each controller call without message metadata starts an independent trace, and
its span ends when that call completes, even while the WebSocket remains open.
An available connection-setup span is linked rather than used as
the call's parent. Explicit per-message W3C context continues the caller's trace.
Clients need no tracing configuration for server-side controller spans; optional
client middleware adds client spans and propagates context for end-to-end traces.
ASGI exclusions do not configure per-operation exclusions; use
`fastapi_canon.integrations.websockets.adapters.opentelemetry.OpenTelemetry` directly
for those controls.

FastAPI disables its native instrumentation when contrib ASGI instrumentation
already owns the middleware stack. Canon's native server-span enrichment then
does not apply. Prefer one HTTP instrumentation owner. SSE receives FastAPI's
normal HTTP tracing; dedicated SSE event instrumentation is outside this API.

## Testing, logs and sampling

`Composition(features=features, request_logging=True)` enables structured HTTP completion and
abort records on `fastapi_canon.access`, independently of `telemetry` and
`Telemetry.logs`. Records carry the HTTP server span's IDs when available.
See [request logging](request-logging.md) for fields, streaming semantics and the
Uvicorn setup for one traceback per propagated exception.

Applications can test traces with the OpenTelemetry SDK’s in-memory span exporter
and a dedicated `TracerProvider`. Pass the provider to `Telemetry` and shut it down
after the test. The repository’s [trace test fixture](../tests/fixtures/telemetry.py)
shows this pattern.

Applications own log correlation via their LoggerProvider/logging handler. Canon
adds no telemetry log pipeline. FastAPI's exception logs can include messages and
stack traces; set `logs=False` or apply application log redaction when needed.
`ParentBased(TraceIdRatioBased(ratio))` lets downstream services follow the
caller's sampling decision. Tail sampling belongs in the collector.
