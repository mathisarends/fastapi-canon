# fastapi-canon

`fastapi-canon` is an opinionated composition library for feature-oriented
FastAPI applications. A feature groups its routers, Dishka providers, error
contracts, exception handlers, and lifespan into one immutable value. The
application installs an explicitly ordered set of those values at its
composition root.

Composition closes HTTP dependency scopes after handler/response validation and
before sending the response. Streams and background work use independent short
scopes. See [databases and service scopes](database.md) for automatic transactions
and Alembic migrations.

Each feature declares its endpoints on one `fastapi_canon.APIRouter`. It keeps
FastAPI's familiar route decorators, adds composable `raises=` and `response=`
contracts, and declares typed SSE (`@router.sse`), JSON-RPC channels
(`router.include_channel`) and WebRTC sessions (`@router.webrtc`) on the same
router. See [routers](routers.md) for every transport option.

Use the [root README walkthrough](../README.md#core-api-routes-errors-and-composition)
for the server-to-SDK path: HTTP, typed SSE and WebSocket RPC declarations become
one Python/TypeScript domain API. This guide covers the core composition and HTTP
options in detail; the [codegen guide](unified-canon-concept.md)
explains shared models, errors and generated bindings.

Dishka is a deliberate part of this canon, not an optional integration.
`fastapi-canon` defines one dependency-injection approach: features contribute
Dishka providers, and the composition builds and owns one shared Dishka
container. Applications that choose another dependency-injection framework are
outside the library's intended architecture.

## Contents

- [Canon architecture and public API](canon.md)
- [Unified package and migration](package-migration.md)
- [Quick start](#quick-start)
- [Feature composition](#feature-composition)
  - [Application router](#application-router)
  - [Router policies](#router-policies)
  - [Dishka](#dishka)
- [Error contracts](#error-contracts)
  - [Optional source checking](error-checking.md)
  - [OpenAPI representation](#openapi-representation)
  - [Shared and operation errors](#shared-and-operation-errors)
  - [Typed server-sent events](#typed-server-sent-events)
- [Success response contracts](#success-response-contracts)
  - [Binary and PDF streams](#binary-and-pdf-streams)
  - [Streaming boundary](#streaming-boundary)
- [Generated domain SDK](#generated-domain-sdk)
- [Low-level FastAPI compatibility](#low-level-fastapi-compatibility)
- [Installation guarantees](#installation-guarantees)
- [Optional OpenTelemetry integration](telemetry.md)
- [Requirements](#requirements)
- [Development](#development)
- [Showcase](#showcase)

## Quick start

Install feature dependencies with `fastapi-canon[sse,websockets,webrtc]` and
generation tooling with `fastapi-canon[codegen]`. `fastapi-canon generate` builds
one domain SDK with shared models and errors; see the
[codegen guide](unified-canon-concept.md).

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature

projects = APIRouter(prefix="/projects", tags=["projects"])


@projects.get("")
async def list_projects() -> list[str]:
    return []


project_feature = Feature(name="projects", routers=[projects])

app = Composition(features=(project_feature,)).build(FastAPI())
```

## Feature composition

Use `composition.check(app)` for a structured preflight report before installation
and `composition.inspect(app)` for an immutable description after installation.
See [composition checks and introspection](system-checks.md) for findings, feature
checks, provenance, CLI commands and coverage boundaries.

Pass an ordered list or tuple with `Composition(features=...)`. Canon copies the
sequence into an immutable tuple, preserving declaration order and validating
duplicate instances and names. The default `features=()` creates an empty
composition. All constructor options are keyword-only.

Every contribution is optional:

```python
from fastapi_canon.integrations.opentelemetry import instrument

feature = Feature(
    name="projects",
    routers=[router],
    providers=[provider],
    instrumentation=instrument(ProjectRepository),
    errors=feature_errors,
    exception_handlers=[handler_spec],
    lifespan=feature_lifespan,
)
```

`instrumentation=` declares ports and asynchronous `__call__` use cases at the
feature boundary. Composition adds their Dishka decorators only when telemetry
is enabled; the original providers remain usable directly. See the
[telemetry guide](telemetry.md) for configuration and migration from `observe()`.

Features automatically collect `Error` definitions from `raises=` declarations
and explicit registries on their routers, including nested routers and channels.
Use `errors=ErrorRegistry(...)` for additional errors that are not declared
anywhere. Repeated references to the same definitions are merged by identity at
the application boundary; collisions between different definitions still fail.

Mutable sequences passed to `Feature` are copied to tuples. Installing features
preserves declaration order for routes and startup. Shutdown runs in reverse
order, including cleanup of features that started before a later feature failed.
Feature names are required, unique within a composition, and included in
configuration diagnostics.

### Application router

Use `router_factory` to create one application router per installed composition.
Feature routers are included in that router before it is mounted on the app, so
the factory can supply a shared prefix, tags, and dependencies:

```python
from fastapi import Depends
from fastapi_canon import APIRouter


async def require_request_id() -> None: ...


composition = Composition(
    features=(project_feature,),
    router_factory=lambda: APIRouter(
        prefix="/api/v1",
        tags=["api"],
        dependencies=[Depends(require_request_id)],
    ),
)
```

The factory is called during `apply()` and must return a fresh, empty router.
Omitting it installs feature routers directly on the app.

### Router policies

Use an immutable `RouterPolicy` to reuse HTTP prerequisites and the errors they
can raise while keeping each router's contract explicit:

```python
from dishka.integrations.fastapi import DishkaRoute
from fastapi import Depends
from fastapi_canon import APIRouter, Composition, RouterPolicy

protected = RouterPolicy(
    dependencies=[Depends(require_authenticated_user)],
    raises=[authentication_required, forbidden],
)

projects = APIRouter(
    prefix="/projects",
    tags=["projects"],
    route_class=DishkaRoute,
    policy=protected,
)


@projects.get("/{project_id}", raises=[project_not_found])
async def get_project(project_id: int) -> ProjectResponse: ...
```

The application supplies the authentication dependency and error definitions.
Dependencies execute through FastAPI; `raises` documents HTTP failures and does
not itself enforce authentication. Policies copy their input sequences to tuples.
Policy, router and operation `raises` automatically register their errors; no
explicit registry is required. The merge keeps the existing registry collision
checks and deduplicates identical Error instances. Policy errors precede router and local
errors; local declarations extend the shared contract. Policy dependencies run
before local router dependencies, with FastAPI's normal dependency caching.

To apply a policy across every feature router in a composition, including nested
routers, pass it at the composition boundary instead:

```python
composition = Composition(
    features=(
        projects_feature,
        users_feature,
    ),
    router_policy=protected,
    router_factory=lambda: APIRouter(prefix="/api/v1"),
)
```

`router_policy` merges its catalog with the feature registries and applies its
dependencies to the installed routes. Its error contracts extend the generated
HTTP operations, including typed SSE operations. Errors sharing a status produce
one combined Problem schema. A conflicting manual `responses=` declaration is
rejected before installation. A policy's `422` error replaces FastAPI's automatic
validation response just as a router-level `raises` declaration does.

Composition policies leave the original feature routers unchanged, so the same
features can be installed in another app with a different policy. Routes mounted
outside the composition do not acquire its prerequisites or declared errors.
Hidden routes execute the dependencies but have no OpenAPI operation to extend.
The application error registry still handles registered exceptions globally;
policy declarations are not a runtime exception allowlist.

`APIRouter(policy=...)` shares error contracts with operations declared directly
on that router, like router-level `raises`. Use `Composition(router_policy=...)`
for contracts across included child routers; the meaning of ordinary `raises=`
and `include_router()` is unchanged. Keep `route_class=DishkaRoute` on the router
where endpoints are declared. A composition policy does not replace route classes
or retrofit injection. WebRTC sessions declare their admission contracts with
`raises=` and `dependencies=` on `@router.webrtc(...)`; composition HTTP contract
compilation does not extend the separate WebRTC session descriptor.

### Dishka

Dishka is a required runtime dependency and the canonical dependency-injection
mechanism. A feature accepts provider instances, provider classes, and
zero-argument factories:

```python
feature = Feature(
    name="projects",
    providers=[ProjectProvider, lambda: DatabaseProvider(settings.database)],
)
```

Classes and factories are materialized once per application during `apply()`;
instances are used as supplied. The providers from every feature are validated
together and used to build one `AsyncContainer`.

`Composition` manages the container lifecycle. It configures Dishka's FastAPI
middleware, exposes the container as `app.state.dishka_container`, and closes
it during application shutdown. Existing application lifespans are composed
automatically. Applications should treat the exposed container as
composition-owned and not close it independently.

### Overriding providers in tests

`composition.override(*providers)` returns a copy of the composition whose
providers are added after every feature provider, so their bindings replace the
feature bindings. The original composition is unchanged:

```python
from dishka import Provider, Scope, provide
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import composition


class FakeProjectProvider(Provider):
    scope = Scope.APP

    @provide
    def repository(self) -> ProjectRepository:
        return InMemoryProjectRepository()


def test_lists_projects() -> None:
    app = composition.override(FakeProjectProvider).build(FastAPI())
    with TestClient(app) as client:
        assert client.get("/projects").json() == []
```

Overrides accept the same provider instances, classes and factories as
`Feature(providers=...)`. An override may only replace bindings that a feature
already provides; a binding for any other type fails with
`canon.providers.E001`, which catches typos and stale fakes.

## Error contracts

The optional [source checker](error-checking.md) finds explicit application
exceptions with missing registry mappings or route `raises=` declarations,
without importing your application:

```console
fastapi-canon check errors src --strict --require-complete
```

`--strict` fails on mapping/declaration gaps. `--require-complete` also fails on
incomplete analysis or no supported operations; unresolved calls and abstract
ports remain visible as incomplete. This checks direct `APIRouter` HTTP
operations; SSE, RPC and WebRTC handlers are outside its current scope.

Error contracts are implemented directly by `fastapi-canon`; no separate error
library is required. Declare `Error` definitions with `raises=`; Composition
collects and installs them once, so runtime RFC 9457 Problem Details and OpenAPI
use the same definitions. Explicit registries add errors not declared elsewhere:

```python
from fastapi_canon import (
    APIRouter,
    Composition,
    Error,
    ErrorOptions,
    Feature,
)


class ProjectNotFound(Exception):
    pass


project_not_found = Error(
    ProjectNotFound,
    status=404,
    code="project_not_found",
    title="Project not found",
    detail="The requested project does not exist.",
)

projects = APIRouter(
    prefix="/projects",
    tags=["projects"],
)
```

Declare operation failures directly, then compose the application:

```python
@projects.get(
    "/{project_id}",
    raises=[project_not_found],
)
async def get_project(project_id: str) -> dict[str, str]:
    raise ProjectNotFound(project_id)


project_feature = Feature(name="projects", routers=[projects])
app = Composition(
    features=(project_feature,),
    errors=ErrorOptions(type_base="https://api.example.com/problems"),
).build(FastAPI())
```

Document normalized `HTTPException` statuses with `http_statuses=[403]` on the
router or operation. A domain error and a generic HTTP
problem may share a status; the generated response then uses the same
discriminated `oneOf` representation.

`type` is the primary RFC 9457 problem identity. `code` is a stable Canon
extension for application convenience. Both describe the same registered
problem; clients should not parse human-readable `title` or `detail` text.
See [shared problem contracts](problems.md) for rendering occurrences and
self-contained schemas outside an HTTP response.

FastAPI `responses={...}` entries without canon error declarations require no
additional metadata. Success contracts and other media types can be declared
alongside problem responses.

#### OpenAPI representation

Every declared error becomes a reusable schema in `components.schemas`. The
corresponding operation references it as an `application/problem+json`
response:

```yaml
paths:
  /projects/{project_id}:
    get:
      responses:
        "404":
          description: Project not found
          content:
            application/problem+json:
              schema:
                $ref: "#/components/schemas/ProjectNotFoundProblem"

components:
  schemas:
    ProjectNotFoundProblem:
      type: object
      required: [type, title, status, code]
      properties:
        type:
          type: string
          const: https://api.example.com/problems/project_not_found
        title:
          type: string
          const: Project not found
        status:
          type: integer
          const: 404
        code:
          type: string
          const: project_not_found
        detail:
          type: string
```

Typed extension fields and documented response headers are added to that same
schema and response. If multiple errors share one status code, the response
uses `oneOf` with the resolved `type` URI as its discriminator. When validation normalization is
enabled, FastAPI's default `422` response is replaced by
`RequestValidationProblem` using the same media type.

When all local registries already share a `type_base`, it is inferred. The
`ErrorOptions` settings `include_validation_error`,
`include_http_exceptions`, and `include_unhandled_error` are passed to
the integrated error engine and default to `True`.

Use `ExceptionHandlerSpec` for a deliberately custom Starlette/FastAPI handler:

```python
from fastapi_canon import ExceptionHandlerSpec

feature = Feature(
    name="projects",
    exception_handlers=[ExceptionHandlerSpec(DomainError, domain_error_handler)],
)
```

`Error.type` (or its registry-resolved URI) is the primary client identity;
`Error.code` is a stable convenience extension. Dynamic `detail`, extensions, and
headers are public response data and should contain only information classified
for API exposure. Exception text can be used as detail when it is itself a
reviewed public contract. Domain registries should use specific exception types;
broad built-ins such as `Exception`, `ValueError`, and `RuntimeError` can also
match unrelated programming failures.

### Shared and operation errors

Errors shared by a router context are declared once on the router; errors that
belong to one operation are declared on that operation. Each declaration also
registers its error definitions:

```python
from fastapi_canon import APIRouter

router = APIRouter(
    prefix="/sessions",
    raises=[authentication_required, session_not_found],
)


@router.post(
    "/{session_id}/playlist/export",
    raises=[playlist_empty, export_target_unavailable],
    status_code=202,
)
async def export_playlist(session_id: str) -> ExportAccepted: ...
```

Canon composes router-level and operation-level errors with the success response
and passes the compiled declaration through FastAPI's normal route API. It does
not patch FastAPI's router or route classes. Its documented `app.openapi` hook
compiles contracts from the generated OpenAPI document without traversing
FastAPI's internal route tree. The standard `get`, `put`, `post`, `delete`,
`options`, `head`, `patch`, and `trace` decorators support `raises=` and
`response=`; `api_route()` supports them for custom method sets.

An explicit error registry is optional. `raises=` contributes definitions to the
application catalog automatically. The same error declared at both levels is
included only once; explicit registry entries add mappings without declaring
them on every operation.
Router-level `raises=` applies to operations declared on that router, not to
separately included child routers; use a [composition policy](#router-policies)
for contracts across routers.

`response=` uses its own status as the route's `status_code` when none is given.
Canon rejects mismatched status codes, bodyless responses with an explicit
`response_model`, and status collisions with FastAPI's manual `responses=`
declarations at route declaration time. Other FastAPI decorator options continue
to pass through unchanged.

Contracts are checked in two places. Each declaration validates its error
definitions when the route is registered, including routes excluded with
`include_in_schema=False`. `Composition.check(app)` and `apply(app)` then compile
every installed operation against the composed error catalog on a temporary
application, before anything is installed; a mismatch is reported as
`canon.contracts.E001` and makes `apply` raise `FeatureConfigurationError`.
`composition.build(app)` applies the composition and repeats the check on the
installed application, including OpenAPI compilation. Prefer it over `apply`
followed by a separate `composition.validate(app)`.

For explicit method lists, import the string enum `HTTPMethod` from
`fastapi_canon`, for example `methods=[HTTPMethod.POST]`. Canon re-exports
Python's standard `http.HTTPMethod`; ordinary string method names also work.

### Typed server-sent events

Install `fastapi-canon[sse]` and declare a typed async generator with
`@router.sse(...)`. The stream shares the router's registry, `raises=` and
`http_statuses=`, and `Feature` discovers its errors like any other operation.
The OpenAPI document keeps the native SSE `itemSchema`/`contentSchema`, so
generated clients receive typed events:

```python
from collections.abc import AsyncIterator

from fastapi import Depends


@router.sse(
    "/{session_id}/events",
    raises=[session_not_found, session_closed],
    dependencies=[Depends(require_existing_session)],
    operation_id="sessions.events",
)
async def session_events(session_id: str) -> AsyncIterator[SessionEvent]:
    async for event in sessions.follow(session_id):
        yield event
```

Run HTTP preconditions in dependencies: even generator code before the first
`yield` runs after the response starts. `raises=` registers and documents the
errors for both phases: an HTTP problem before response start, or a terminal
`canon.problem` during processing. Generated iterators yield business events, consume
completion frames and raise `ProblemError` for declared terminal problems.
Connection failures remain transport errors. See
[shared problem bindings](problems.md).

See the [streams guide](streams.md) for Dishka injection, POST bodies and the
streaming boundary. [`examples/streams`](../examples/streams) is an executable
GET/POST app.

## Success response contracts

`CanonResponse` describes one successful response that FastAPI cannot infer from
the return annotation, such as an empty, binary or custom media-type response:

```python
from fastapi_canon import CanonResponse


@router.delete(
    "/{project_id}",
    raises=[project_not_found],
    response=CanonResponse.empty(),
)
async def delete_project(project_id: str) -> None: ...
```

These declarations are self-contained: a success-only router produces clean
OpenAPI without installing an `ErrorRegistry` or another OpenAPI hook.

The generic form accepts a status, media type, OpenAPI schema, description, and
response headers:

```python
success = CanonResponse(
    status=200,
    media_type="application/example+json",
    schema={"type": "object"},
    description="Example document",
    headers=["X-Request-ID"],
)
```

The available constructors are:

| Constructor | Default contract |
| --- | --- |
| `CanonResponse.json()` | `application/json` with status 200 |
| `CanonResponse.empty()` | No response content with status 204 |
| `CanonResponse.stream(media_type)` | String stream with status 200 |
| `CanonResponse.binary(media_type)` | Binary string stream with status 200 |

Typed event streams use `@router.sse(...)` instead of a hand-written
`text/event-stream` response.

Success-only routers do not need an empty `ErrorRegistry`:

```python
@router.get(
    "/health",
    response=CanonResponse.json(
        schema={"type": "object"},
        description="Service health",
    ),
)
async def health() -> dict[str, str]: ...
```

The route declaration is independent of the application's error definitions.

Bodyless contracts also replace FastAPI's generated success content for 2xx and
3xx responses, so a redirect needs no matching `response_class` solely for
OpenAPI purposes:

```python
@router.get(
    "/elsewhere",
    response=CanonResponse.empty(status=307),
)
async def elsewhere() -> RedirectResponse: ...
```

A bodyless contract combined with an explicit `response_model` raises
`ResponseConfigurationError` at declaration time.

Schemas and header definitions are copied into immutable mappings. Header names
may be supplied as a list for standard string-valued header schemas or as a
mapping containing complete OpenAPI header definitions. The router derives
the route status from the success contract when `status_code` is omitted.

### Binary and PDF streams

```python
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from fastapi.responses import StreamingResponse
from fastapi_canon import APIRouter, CanonResponse


router = APIRouter(
    error_registry=document_errors,
    raises=[AUTHENTICATION_REQUIRED_ERROR, ACCESS_DENIED_ERROR],
)


def pdf_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            yield chunk


@router.get(
    "/documents/{document_id}",
    raises=[document_missing],
    response=CanonResponse.binary(
        "application/pdf",
        headers=["Content-Disposition"],
    ),
)
async def document(document_id: UUID) -> StreamingResponse:
    path = Path("documents") / f"{document_id}.pdf"
    if not path.is_file():
        raise DocumentMissing
    return StreamingResponse(
        pdf_chunks(path),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{document_id}.pdf"',
        },
    )
```

The generated operation documents only `application/pdf` for the successful
response. Error responses continue to use
`application/problem+json`.

### Streaming boundary

Authentication, authorization, validation, and resource lookup should complete
before returning `StreamingResponse`. Once response headers have been sent, an
exception inside the iterator cannot be converted into another HTTP response; a
failed binary iterator produces an incomplete download. Typed SSE streams report
such failures as terminal `canon.problem` events instead.

## Generated domain SDK

Use `fastapi-canon generate` to export HTTP/SSE and OpenRPC contracts and generate
one domain API with shared public models and errors. Consumers call
`client.projects.get(...)`, iterate `client.projects.events(...)`, or await
`client.projects.cancel(...)`; protocol bindings handle their wire behavior.

The [README examples](../README.md#generate-and-use-the-sdk) show the complete
configuration and Python/TypeScript calls. The
[codegen guide](unified-canon-concept.md) documents naming, dependency
requirements, operation selection and supported schemas. Run
`fastapi-canon generate --check --diff` in CI to detect drift without rewriting
files. Generated runtimes do not import Canon.

## Low-level FastAPI compatibility

Routers that Canon does not create, such as generated or third-party
`fastapi.APIRouter` instances, can still declare contracts through FastAPI's
`responses=` mapping:

```python
from fastapi import APIRouter as FastAPIRouter
from fastapi_canon import CanonResponse, install_openapi_contracts

generated_router = FastAPIRouter()


@generated_router.get(
    "/private",
    responses=api_errors.responses(
        project_not_found,
        http_statuses=[401, 403],
        success=CanonResponse.json(),
    ),
)
async def private_project() -> dict[str, str]: ...


install_openapi_contracts(app)
```

`ErrorRegistry.responses()` combines domain errors, normalized `HTTPException`
statuses, and an optional success contract; `CanonResponse.responses()` covers
success-only declarations. These primitives populate FastAPI's `responses={...}`
mapping directly, so they skip the declaration-time consistency checks of
`fastapi_canon.APIRouter`. A manually assembled application must call
`install_openapi_contracts(app)` before generating OpenAPI. `Composition`
installs that compiler automatically, including when no error registry exists.

## Installation guarantees

- Feature order is explicit and deterministic.
- Feature names identify conflicting contributions in diagnostics.
- Reinstalling the exact same feature objects with the same options is a no-op.
- A different second installation is rejected.
- Duplicate routers, providers, handlers, and error collisions fail during
  configuration.
- Registry, handler and operation contracts are checked against a temporary
  application before installation.
- Provider-backed features must be installed before the application starts.
- Disabling a feature means omitting it from `Composition`, which removes all of
  its contributions together.

Configuration failures raise `FeatureConfigurationError`.

## Requirements

- CPython 3.12, 3.13, or 3.14
- FastAPI 0.121 or newer, below 1.0
- Dishka 1.10 or newer, below 2.0
- Pydantic 2.9 or newer, below 3.0

## Development

Run the shared development commands from the repository root:

```console
uv sync --locked --all-groups
uv run --locked pre-commit install
uv run --locked python scripts/check.py all
uv run --locked python scripts/check_distributions.py
```

## Showcase

See [`examples/showcase`](../examples/showcase) for a runnable two-feature FastAPI
application. It keeps error contracts alongside their feature routes and merges
them once at the composition root.
