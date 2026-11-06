# Components and composition

Use this to identify the pieces of an existing Canon application or wire a new
feature. Inspect the installed signatures before relying on an option:

```console
uv run fastapi-canon --version
uv run python -c "import inspect; from fastapi_canon import Composition, Feature; print(inspect.signature(Composition)); print(inspect.signature(Feature))"
```

## Component map

| Component | Responsibility | Connect it through |
| --- | --- | --- |
| `APIRouter` | One feature router: HTTP contracts, typed SSE, JSON-RPC channel mounts, WebRTC sessions | `Feature(routers=[router])` |
| `RpcChannel`, `Inject` | Typed JSON-RPC methods/events and their injected dependencies | `router.include_channel(channel, path=...)` |
| `Error` | Public problem identity and exception mapping | `raises=` registers definitions; `Feature(errors=[...])` adds undeclared errors; `ErrorRegistry` only for manual assembly |
| `RouterPolicy` | Reusable HTTP prerequisites and documented failures | Router `policy=`; an including or `router_factory` router extends its children |
| Dishka `Provider` | Bind application services and ports to their implementations | `Feature(providers=[ProviderClass])` |
| `Feature` | Immutable contributions of one feature: routers, providers, errors, lifespan, checks, instrumentation, MCP mounts | Ordered `Composition(features=(...))` |
| `Composition` | Install routes, error contracts and one Dishka container; own startup and cleanup | `composition.build(FastAPI())` |
| `Database`, `AlembicMigrations` | Optional engine, sessions, transactions and migration configuration | `database.feature` before consuming features |
| `ServiceScope[T]` | An independent short dependency graph for a worker/stream operation | APP-scoped provider; `async with scope() as service:` |
| `Telemetry`, `instrument` | Optional tracing/metrics and observed application ports | `Composition(telemetry=...)`, `Feature(instrumentation=...)` |
| `McpServer`, `McpMount`, `McpOptions` | Optional tools/resources/prompts on one app-owned MCP endpoint | `Feature(mcp=[...])`, `Composition(mcp=McpOptions())` |
| `export_contracts`, CLI `contracts export`, `generate` | Export the composed API and generate Python/TypeScript clients | App/factory or saved contracts; explicit SDK configuration |

Import core values from `fastapi_canon` or `fastapi_canon.error`. MCP and telemetry
use their `fastapi_canon.integrations.*` facades. Extras install dependencies;
enable only those the application needs.

## Wiring in one place

The feature's `feature.py` groups its already declared router and provider.
Provider classes must be constructible without arguments; use a factory for
configuration-dependent providers. Application services receive ports in their
constructors and remain independent of Canon and Dishka.

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, ErrorOptions, Feature

from backend.features.projects.infrastructure.di import ProjectsProvider
from backend.features.projects.presentation.router import router

projects = Feature(name="projects", routers=[router], providers=[ProjectsProvider])


def create_app() -> FastAPI:
    composition = Composition(
        features=(projects,),
        errors=ErrorOptions(type_base="https://api.example.com/problems"),
        router_factory=lambda: APIRouter(prefix="/api/v1"),
    )
    return composition.build(FastAPI(title="Projects API"))
```

`build()` validates before and after installing. Composition creates the container during
lifespan, starts features in declaration order and shuts them down in reverse.
Feature lifespans may borrow dependencies while the container is alive. Keep
connections and I/O out of imports, declarations and app construction; open owned
resources in yielding providers or lifespans. Do not add a second `setup_dishka`
or manually install the same error registry.

HTTP handlers, SSE endpoints and JSON-RPC methods use `Inject[T]`;
Composition's container resolves them. FastAPI dependency functions that use
`FromDishka[T]` still need `@inject`.
APP resources own pools and
factories; REQUEST resources own a single operation. See the
[HTTP walkthrough](http-feature.md) for the complete router/service/provider and
[resources](resources.md) before adding persistence or long-lived work.

Inspect the composed app and export its contracts:

```console
uv run fastapi-canon inspect backend.app:create_app --factory
uv run fastapi-canon contracts export --app backend.app:create_app --output-dir contracts
```

These commands import/build the app without entering lifespan. Use
`composition.check(app)` before installation for structured diagnostics and
`composition.inspect(app)` afterwards for installed HTTP ownership and provenance.
Do not assume they validate every transport's runtime behavior.

Read [transports and SDKs](transports-and-sdk.md) only when exposing another
protocol or generating clients. For MCP, consult the
[MCP guide](https://github.com/mathisarends/fastapi_canon/blob/main/docs/integrations/mcp/README.md)
for separate injection, authentication and scope rules. HTTP dependencies do not
protect the MCP mount, and this integration does not generate MCP clients.
