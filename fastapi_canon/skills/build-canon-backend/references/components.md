# Components and composition

Use this to identify the pieces of an existing Canon application or wire a new
feature. Inspect installed signatures when a project pins an older version:

```console
uv run fastapi-canon --version
uv run python -c "import inspect; from fastapi_canon import Composition, Feature; print(inspect.signature(Composition)); print(inspect.signature(Feature))"
```

## Component map

| Component | Responsibility | Connect it through |
| --- | --- | --- |
| `APIRouter` | HTTP contracts, typed SSE, RPC channel mounts, WebRTC declarations | `Feature(routers=[router])` |
| `CanonRouter` | HTTP-only route and error contracts; existing specialized transport routers remain supported | `Feature(routers=[router])` |
| `Error`, `ErrorRegistry` | Public problem identity and exception mapping | Router `error_registry=`, operation `raises=` |
| `RouterPolicy` | Reusable HTTP prerequisites and documented failures | Router `policy=` or composition `router_policy=` |
| Dishka `Provider` | Bind application services and ports to their implementations | `Feature(providers=[ProviderClass])` |
| `Feature` | Immutable contributions of one feature: routers, providers, errors, lifespan, checks, instrumentation, MCP mounts | Ordered `Composition(features=(...))` |
| `Composition` | Install routes, error contracts and one Dishka container; own startup and cleanup | `composition.apply(FastAPI())` |
| `Database`, `AlembicMigrations` | Optional engine, sessions, transactions and migration configuration | `database.feature` before consuming features |
| `ServiceScope[T]` | An independent short dependency graph for a worker/stream operation | APP-scoped provider; `async with scope() as service:` |
| `Telemetry`, `instrument` | Optional tracing/metrics and observed application ports | `Composition(telemetry=...)`, `Feature(instrumentation=...)` |
| `McpServer`, `McpMount`, `McpOptions` | Optional tools/resources/prompts on one app-owned MCP endpoint | `Feature(mcp=[...])`, `Composition(mcp=McpOptions())` |
| `export_contracts`, CLI `contracts export`, `generate` | Export the composed API and generate Python/TypeScript clients | App/factory or saved contracts; explicit SDK configuration |

Import core values from `fastapi_canon` or `fastapi_canon.error`. MCP and telemetry
use their `fastapi_canon.integrations.*` facades. Extras install dependencies;
enable only those the application needs. Older releases may require specialized
routers in place of the shared `APIRouter`; verify availability before migrating.

## Wiring in one place

The feature's `feature.py` groups its already declared router and provider.
Provider classes must be constructible without arguments; use a factory for
configuration-dependent providers. Application services receive ports in their
constructors and remain independent of Canon and Dishka.

```python
from fastapi import FastAPI
from fastapi_canon import CanonRouter, Composition, ErrorOptions, Feature

from backend.features.projects.infrastructure.di import ProjectsProvider
from backend.features.projects.presentation.router import router

projects = Feature(name="projects", routers=[router], providers=[ProjectsProvider])


def create_app() -> FastAPI:
    composition = Composition(
        features=(projects,),
        errors=ErrorOptions(type_base="https://api.example.com/problems"),
        router_factory=lambda: CanonRouter(prefix="/api/v1"),
    )
    return composition.apply(FastAPI(title="Projects API"))
```

`apply()` validates before installing. Composition creates the container during
lifespan, starts features in declaration order and shuts them down in reverse.
Feature lifespans may borrow dependencies while the container is alive. Keep
connections and I/O out of imports, declarations and app construction; open owned
resources in yielding providers or lifespans. Do not add a second `setup_dishka`
or manually install the same error registry.

HTTP handlers use `DishkaRoute` and `FromDishka[T]`. APP resources own pools and
factories; REQUEST resources own a single operation. See the
[HTTP walkthrough](http-feature.md) for the complete router/service/provider and
[resources](resources.md) before adding persistence or long-lived work.

For installed versions providing composition diagnostics and contract export:

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
