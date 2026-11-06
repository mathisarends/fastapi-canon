# SSE routes and HTTP contracts

Select the SSE dependencies:

```console
pip install "fastapi-canon[sse]"
```

Declare typed streams with `@router.sse(...)` on the feature's
`fastapi_canon.APIRouter`. The `sse` extra selects FastAPI >=0.143.3 and
Pydantic >=2.11.2; base composition keeps its lower bounds, and core imports do
not load the SSE runtime.

## Complete integration example

Save this as `app.py` and run `uvicorn app:app` with Uvicorn installed:

```python
from collections.abc import AsyncIterator
from typing import Annotated

from dishka import AsyncContainer, Provider, Scope, provide
from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel

from fastapi_canon import (
    APIRouter,
    Composition,
    Error,
    ErrorOptions,
    Feature,
    Inject,
    ServiceScope,
)


class AuthenticationRequired(Exception):
    pass


class RunNotFound(Exception):
    pass


class RunEvent(BaseModel):
    run_id: str
    message: str


class RunService:
    async def require_existing(self, run_id: str) -> None:
        if run_id != "demo":
            raise RunNotFound

    async def events(self, run_id: str) -> AsyncIterator[RunEvent]:
        yield RunEvent(run_id=run_id, message="Started")
        yield RunEvent(run_id=run_id, message="Completed")


class RunProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def run_service(self) -> RunService:
        return RunService()

    @provide(scope=Scope.APP)
    def run_scope(self, container: AsyncContainer) -> ServiceScope[RunService]:
        return ServiceScope(container, RunService)


authentication_required = Error(
    AuthenticationRequired,
    status=401,
    code="authentication_required",
    title="Authentication required",
)
run_not_found = Error(
    RunNotFound, status=404, code="run_not_found", title="Run not found"
)


async def require_authentication(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if authorization != "Bearer demo":
        raise AuthenticationRequired


async def require_existing_run(run_id: str, runs: Inject[RunService]) -> None:
    await runs.require_existing(run_id)


router = APIRouter(
    prefix="/runs",
    raises=[authentication_required],
    dependencies=[Depends(require_authentication)],
)


@router.sse(
    "/{run_id}/events",
    raises=[run_not_found],
    dependencies=[Depends(require_existing_run)],
    operation_id="run_events",
)
async def events(
    run_id: str, runs: Inject[ServiceScope[RunService]]
) -> AsyncIterator[RunEvent]:
    async with runs() as service:
        snapshot = [event async for event in service.events(run_id)]
    for event in snapshot:
        yield event


feature = Feature(name="runs", routers=[router], providers=[RunProvider])
composition = Composition(
    features=(feature,),
    errors=ErrorOptions(type_base="https://api.example.com/problems"),
)
app = composition.build(FastAPI())
```

Request `GET /runs/demo/events` with `Authorization: Bearer demo` to receive SSE.
Omit the header for a 401 problem, or request another run ID for a 404 problem.
`Feature` automatically collects the errors declared by the router; no explicit
registry is required. `Composition` installs the
runtime handlers and OpenAPI compiler together.

## Dishka injection

`Feature(providers=[RunProvider])` lets `Composition` build the Dishka container,
install its FastAPI middleware, and manage shutdown. No separate
`make_async_container()` or `setup_dishka()` call is needed. Dishka is already
a canon dependency.

The precondition injects a request-scoped `RunService`. That scope closes before
the response starts. The generator resolves an APP-scoped `ServiceScope` factory
and opens a fresh short scope to materialize this finite example's events,
closing it before yielding frames. For an unbounded stream, fetch short batches
in separate scopes rather than materializing the entire stream or keeping a
database session open. APP-scoped services can also be injected directly.

`Inject[T]` resolves application services in HTTP and SSE endpoints and in
ordinary FastAPI dependency functions such as `require_existing_run`, without
`@inject`. `APIRouter` also resolves Dishka's `FromDishka[T]` in endpoint
parameters; dependency functions using `FromDishka[T]` still need Dishka's
`@inject`. Run the resource check in that dependency: the generator's own parameters are
resolved on its first iteration, after the response has started, and a failure
there becomes a terminal stream problem rather than an HTTP problem. Resolve any
service that must fail as an HTTP problem in a dependency first. See
[Dishka's FastAPI integration](https://dishka.readthedocs.io/en/stable/integrations/fastapi.html)
for Dishka's own markers and dependency injection details.

## Router and operation options

| API | Behavior |
| --- | --- |
| `APIRouter(raises=[...], http_statuses=[...], **options)` | Router-level `raises` automatically registers shared errors and applies to every HTTP and SSE operation declared on this router. Standard FastAPI router options pass through. |
| `router.sse(path, raises=[...], methods=None, **options)` | Local errors extend shared errors for both admission and terminal stream failures. Defaults to GET; use `methods=[HTTPMethod.POST]` for a body-driven stream. FastAPI dependencies, tags and operation IDs pass through. |
| `router.get(...)`, `router.post(...)`, `router.api_route(...)` | Ordinary HTTP declarations on the same router. |

### HTTP methods

`HTTPMethod` is available from `fastapi_canon`. It re-exports Python's standard
`http.HTTPMethod`, a string enum with `GET`, `POST`, `PUT`, `PATCH`, `DELETE`,
`HEAD`, `OPTIONS`, `TRACE`, and `CONNECT`. It works in normal FastAPI/canon
`methods=` lists too; strings remain supported.

For a separate POST operation, add this before constructing the feature/app:

```python
from http import HTTPMethod


@router.sse(
    "/{run_id}/events",
    methods=[HTTPMethod.POST],
    raises=[run_not_found],
    dependencies=[Depends(require_existing_run)],
    operation_id="create_run_events",
)
async def create_events(
    run_id: str, runs: Inject[RunService]
) -> AsyncIterator[RunEvent]:
    async for event in runs.events(run_id):
        yield event
```

`methods=[HTTPMethod.POST]` selects **only POST**, following FastAPI's
`api_route()` convention. It can also select both
`[HTTPMethod.GET, HTTPMethod.POST]` for the same handler. SSE accepts GET and
POST only; other enum members are rejected.
GET is useful for subscribing to events; POST lets the request include a typed
JSON body, as in [`examples/streams/app.py`](../examples/streams/app.py).
For generated clients, declare GET and POST separately with different
`operation_id` values, as the runnable example does.

Every declared error must be an `Error` instance and is registered automatically.
Duplicate instances, including errors
declared at both levels, appear once. Distinct errors with the same HTTP status
use canon's discriminated `oneOf` problem schema. Invalid declarations raise
`ErrorConfigurationError` during router construction or endpoint registration.
Shared `raises` does not propagate to included child routers.

Use an annotated generator (`AsyncIterator[RunEvent]`, or `Iterator[RunEvent]`
for synchronous endpoints). Canon infers the successful SSE response from the
annotation and rejects incompatible methods, response models, and serialization
options. Typed POST bodies and a
discriminated event union are shown in the executable
[`examples/streams/app.py`](../examples/streams/app.py).

To document normalized `HTTPException` statuses, pass `http_statuses=[403]` to
the router or the operation. Request validation errors are normalized and
documented by `Composition` by default.

## Export and client generation

Use [`fastapi-canon generate`](unified-canon-concept.md) for a
domain SDK that combines HTTP, SSE and WebSocket RPC with shared public models
and errors. SSE uses the same OpenAPI source as HTTP; annotate the event type
and set `namespace="projects"` on the router with a function named `events`, or
choose an explicit name such as `operation_id="projects.events"`, to generate
`client.projects.events(...)`. Python and TypeScript consumers use async
iteration. The binding handles framing, decoding, terminal problems and
completion. The [quickstart](quickstart.md#streams-typed-events-from-server-to-sdk)
shows the server declaration, configuration and both client languages.
See [automatic operation names](routers.md#automatic-http-and-sse-operation-names)
for naming precedence, stable SDK names and collision checks in the Canon CLI.

The exported OpenAPI 3.2 document keeps the complete native `itemSchema` and
JSON `contentSchema`, including referenced models and event discriminators, and
the declared `application/problem+json` responses. Export it from this repository
with:

```console
uv run --locked fastapi-canon contracts export --app examples.streams.app:app --output-dir contracts
```

Another application installs `fastapi-canon[codegen]` and uses its own module
path.

## Streaming boundary

- **Before response start:** authentication, authorization, and resource checks
  in dependencies can raise domain exceptions. Canon returns RFC 9457 HTTP
  problems, including normalized HTTP and request validation errors.
- **During streaming:** every generator statement, even before its first
  `yield`, executes after response start. Declare public domain failures with
  `raises=[...]`; Canon emits a terminal `canon.problem` frame and closes
  the stream. Normal exhaustion emits `canon.complete`.
- **Connection failures:** disconnects remain transport errors.

`raises=` registers and documents errors for both HTTP admission and stream
processing; it does not move generator execution before response start. The
failure phase determines the wire representation. Generated iterators yield only
business events, raise `ProblemError` for terminal problems, and distinguish
completion from unexpected EOF (`TransportError`). Undeclared processing errors
produce a safe internal problem. See [canonical error bindings](problems.md).

## Compatibility regression checks

The [root CI workflow](../.github/workflows/ci.yml) checks the locked repository on Linux with
Python 3.12–3.14 and Windows with Python 3.14. Regression coverage includes
dependency problems, typed GET/POST streams, complete SSE schemas, both client
generators and Dishka lifetime/cleanup. Installed-wheel checks also exercise
Canon without the SSE extra. Run the focused suite from the repository root:

```console
uv run --locked python -m pytest tests/integration/integrations tests/integration/test_optional_streams.py
```
