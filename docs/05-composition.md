# 5. Composition

Grow from one router to an application: features bundle routes, services and
startup code, and `Composition` assembles them into one FastAPI app.

## Features

```python
from dishka import Provider, Scope, provide
from fastapi import FastAPI

from fastapi_canon import APIRouter, Composition, Feature, Inject


class ProjectProvider(Provider):
    repository = provide(
        SqlProjectRepository, provides=ProjectRepository, scope=Scope.REQUEST
    )
    service = provide(ProjectService, scope=Scope.REQUEST)


router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("/{project_id}", raises=[project_not_found])
async def get_project(project_id: str, service: Inject[ProjectService]) -> Project:
    return await service.get(project_id)


projects = Feature(name="projects", routers=[router], providers=[ProjectProvider])
app = Composition(features=[projects]).build(FastAPI())
```

A feature is everything one slice of your app contributes. `Composition` builds
one Dishka container from all providers, installs routes in order, collects
every declared error and validates the result before the app starts.

`Inject[T]` works in routes, SSE streams, RPC methods and ordinary FastAPI
dependencies, without `@inject`.

## Feature lifespans

Start and stop background work with the feature that owns it:

```python
@asynccontextmanager
async def worker_lifespan(worker: Inject[ProjectWorker]) -> AsyncGenerator[None]:
    worker.start()
    try:
        yield
    finally:
        await worker.stop()


projects = Feature(name="projects", providers=[provider], lifespan=worker_lifespan)
```

Lifespans start in feature order and stop in reverse. Injected services must be
APP-scoped.

## Router policies

Share authentication and its errors across routers:

```python
from fastapi_canon import RouterPolicy

protected = RouterPolicy(
    dependencies=[Depends(require_user)],
    raises=[authentication_required, forbidden],
)

projects = APIRouter(prefix="/projects", policy=protected)
health = APIRouter(prefix="/health")  # stays public
```

A policy applies to its router and everything that router includes.

## A shared prefix

```python
composition = Composition(
    features=[projects, users],
    router_factory=lambda: APIRouter(prefix="/api/v1"),
)
```

Pass `policy=protected` to that router to protect every feature at once.

## Middleware

```python
composition = Composition(
    features=[projects],
    middleware=[Middleware(CORSMiddleware, allow_origins=origins)],
)
```

Use `outer_middleware=` for middleware that wraps FastAPI's full stack, such as
CORS for [app-wide upload limits](08-file-transfers.md#limits). List each group
outermost first. Both groups resolve `Inject[T]` constructor parameters from the
application container.

## Integrations

Optional integrations are enabled once on the composition; features contribute
parts to them:

```python
projects = Feature(
    name="projects",
    routers=[router],
    integrations=[
        McpMount(server=projects_mcp, namespace="projects"),
        instrument(ProjectRepository),
    ],
)
app = Composition(
    features=[projects],
    integrations=[Mcp(path="/mcp"), Telemetry()],
).build(FastAPI())
```

A part without its integration fails at build time, so a feature can never open
an MCP endpoint on its own. See [database](09-database.md),
[observability](10-observability.md) and [MCP](12-mcp.md).

## Overriding providers in tests

```python
def test_lists_projects() -> None:
    app = composition.override(FakeProjectProvider).build(FastAPI())
    with TestClient(app) as client:
        assert client.get("/projects").json() == []
```

Overrides may only replace existing bindings, so typos and stale fakes fail.

## Outside the app

Scripts, workers and CLI commands can open the same container:

```python
async with composition.container() as container:
    async with container(scope=Scope.REQUEST) as scoped:
        await (await scoped.get(SeedAdmin))(email, password)
```

## Check before you deploy

```console
fastapi-canon check composition --app app.main:app
```

Reports missing providers, conflicting errors and undocumented responses. See
[checks and diagnostics](23-checks-and-diagnostics.md).

Runnable: [feature showcase](../examples/showcase/README.md).

---

Previous: [4. WebSockets](04-websockets.md) · [Learning path](README.md) · Next: [6. Client generation](06-codegen.md)
