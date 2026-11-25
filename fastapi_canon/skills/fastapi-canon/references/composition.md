# Composition

| Component | Purpose | Connect through |
| --- | --- | --- |
| `APIRouter` | HTTP, typed SSE, RPC channel mounts | `Feature(routers=[...])` |
| `WebRtcRouter` | Media sessions (`@rtc.session`) | `Feature(routers=[...])` |
| `Error` | Public problem + exception mapping | `raises=[...]`; `Feature(errors=[...])` for undeclared ones |
| `RouterPolicy` | Shared prerequisites and documented failures | `APIRouter(policy=...)` |
| Dishka `Provider` | Bind services and ports | `Feature(providers=[...])` |
| `Feature` | One feature's routers, providers, errors, lifespan, checks, integrations | `Composition(features=(...))` |
| `Composition` | Routes, errors, one container, startup/shutdown | `composition.build(FastAPI())` |
| `Database`, `Tables` | Engine, sessions, transactions | [resources.md](resources.md) |
| `Telemetry`, `instrument` | Tracing and metrics | See below |
| `Mcp`, `McpMount` | MCP tools on one endpoint | [transports.md](transports.md#mcp) |

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, ErrorOptions

from backend.composition import FEATURES

composition = Composition(
    features=FEATURES,
    errors=ErrorOptions(type_base="https://api.example.com/problems"),
    router_factory=lambda: APIRouter(prefix="/api/v1"),
)


def create_app() -> FastAPI:
    return composition.build(FastAPI(title="Projects API"))
```

- All `Composition` and `Feature` arguments are keyword-only.
- `build()` validates checks and OpenAPI. Lifespans start in feature order and
  stop in reverse; they borrow APP services via `Inject[T]` parameters.
- No I/O at import or construction time; open resources in yielding providers.
- Provider classes must construct without arguments; use a factory otherwise.
- Tests replace bindings with `Composition(..., overrides=[TestProvider])`.

Inspect without starting the app:

```console
uv run fastapi-canon inspect backend.app:create_app --factory
uv run fastapi-canon check composition backend.app:composition
```

## Logging and telemetry

`Composition(request_logging=True)` emits structured request records; the app
configures logging outputs. For tracing install the `otel` extra:

```python
from fastapi_canon.integrations.opentelemetry import Telemetry, instrument

Feature(name="projects", ..., integrations=[instrument(ProjectService)])
Composition(features=FEATURES, integrations=[database, Telemetry(...)])
```

Place `Telemetry` after `Database`. The app owns exporter shutdown. Details:
[observability](https://github.com/mathisarends/fastapi-canon/blob/main/docs/10-observability.md),
[composition](https://github.com/mathisarends/fastapi-canon/blob/main/docs/05-composition.md).
