# One Canon contract

Canon's composition, exception mapping, response declarations, and OpenAPI
compiler describe one application. OpenAPI is the input to HTTP client
generation. There is no separate Canon schema language or server scaffolder.

## Ownership

| Concept | Responsibility |
| --- | --- |
| Application exception | A domain failure, independent of FastAPI and transport |
| `Error` | Maps one exception class to a stable code, status, title, public fields, and headers |
| `ErrorRegistry` | An immutable catalog; validates collisions and resolves exceptions by Python MRO |
| `APIRouter` | Declares a feature's HTTP, SSE, JSON-RPC and WebRTC operations with their errors, using familiar FastAPI decorators |
| `CanonResponse` | Describes a success contract; endpoints still return FastAPI-compatible values or responses |
| `Feature` | Owns routers, registries, Dishka providers, handlers, and a lifespan |
| `Composition` | Installs features and owns app-wide normalization, validation, and container lifecycle |
| `Problem` | The runtime RFC 9457 payload shared by all HTTP failures Canon normalizes |
| Compiled OpenAPI | The public HTTP contract consumed by documentation, clients, and agents |

Define domain exceptions in application code. Map them once with `Error`, collect
them in a registry, and declare the relevant errors through `raises=`. Error
callbacks explicitly choose public details; exception text is not automatically
exposed. Exceptions are resolved by the most specific registered class.

`raises=` documents possible failures. It does not catch exceptions, restrict
runtime exceptions, or turn an endpoint into an exhaustive checked operation.
Installed mappings apply throughout the application, including dependencies.
Unexpected HTTP failures become safe internal problems by default.

## Canonical path

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Error, ErrorRegistry, Feature


class ProjectMissing(Exception):
    pass


project_missing = Error(
    ProjectMissing,
    status=404,
    code="project_missing",
    title="Project missing",
)
registry = ErrorRegistry(errors=[project_missing])
router = APIRouter(prefix="/projects", error_registry=registry)


@router.get("/{project_id}", operation_id="get_project", raises=[project_missing])
async def get_project(project_id: str) -> dict[str, str]:
    raise ProjectMissing(project_id)


composition = Composition(features=[Feature(name="projects", routers=[router])])
app = composition.apply(FastAPI())
composition.validate(app)
```

The route references the error definition. The feature collects its registry.
Composition merges the catalogs once, installs runtime handlers, and compiles
the same definitions into OpenAPI. Shared definitions are reused by identity;
separate definitions colliding on code, exception class, type URI, or schema name
fail configuration. Build routers before creating their features.

Give each feature one `APIRouter`. Router-level `raises=` and `http_statuses=`
apply to every operation declared directly on it, including SSE streams. Routers
added with `include_router()` keep their own contracts. Ordinary FastAPI routers
remain available; contribute their catalogs through `Feature(errors=...)`.

`http_statuses=[401, 403]` documents generic normalized `HTTPException` failures.
It does not introduce authentication or authorization behavior. A domain error
is preferable when clients need a specific business meaning.

## Application policy and lifecycle

`ErrorOptions` sets normalization policy once at the composition root. By
default, HTTP exceptions, request validation, response validation, and unexpected
HTTP exceptions use `application/problem+json`, even in an app with no domain
errors. Domain mappings preserve their resolved URIs. Unresolved mappings use
the explicit or inferred application type base, falling back to
`urn:fastapi-canon:problem`.

Features install before startup. Contract validation runs before application,
router, or feature lifespan resources open. Lifespans unwind in reverse order,
and the shared Dishka container closes after those resources. Prefer
`Composition` over calling registry installation or Dishka setup independently.
The low-level APIs exist for manually assembled FastAPI applications.

Use `ExceptionHandlerSpec` only when a deliberately custom handler is needed.
Its response contract belongs to the application. Overlapping registrations
fail instead of silently overriding each other. Catch `ConfigurationError` to
handle all Canon configuration failures at a build or tooling boundary.

## Code generation and agents

1. Build the application through `Composition.apply()`, which checks every
   operation contract before installation.
2. Export the composed app with `export_contracts(app)` or
   `fastapi-canon contracts export --app backend.app:app --output-dir contracts`.
3. Generate clients with `fastapi-canon generate --config fastapi-canon.toml`, or
   give the OpenAPI document to the chosen generator.
4. Test important runtime payloads against their generated response schemas.

Use stable, unique `operation_id=` values for public client methods. FastAPI's
default IDs include routing details; changing paths can change those IDs. Canon
retains FastAPI's default naming and rejects duplicate IDs. A multi-method
`api_route()` therefore needs distinct operations when used for client generation.
See [FastAPI's client generation guide](https://fastapi.tiangolo.com/advanced/generate-clients/).

Client code should branch on `code`. Treat `title` and `detail` as human-readable
text. Errors sharing one status use reusable component references and a `oneOf`
discriminated by `code`, including framework validation and internal failures.
The document contains no private Canon declaration metadata. Explicit manual
schemas and custom handlers require application-owned contract tests.

CI smoke-tests Python client generation from the showcase with
[`openapi-python-client`](https://github.com/openapi-generators/openapi-python-client).
Other generators should support OpenAPI 3.1, `const`, and discriminated `oneOf`
schemas; validate your chosen generator against the operations it will consume.

For coding agents, the default recipe is: define a specific exception, create
one `Error`, register it, declare it through `raises=`, and contribute its router
through a named feature. Add `http_statuses=` only for generic HTTP failures.
Use `response_model=` for typed JSON and `response=CanonResponse...` when an
explicit media type, stream, or bodyless contract is needed.

## Transport boundaries

| Transport | Before the response starts | After the response starts |
| --- | --- | --- |
| HTTP JSON or binary | Normalized Problem Details | The response cannot be replaced |
| SSE | Normalized Problem Details during authentication and setup | The application may define an error event; otherwise the stream fails |
| WebSocket | Problem Details denial if the server supports it, otherwise close | Close 1008 for client problems, 1011 for server problems; stable code as reason |

Complete authentication, authorization, and initial resource lookup before
returning a streaming response. Iterator failures cannot become a second HTTP
response. Canon does not invent an SSE event format or a WebSocket message
envelope. Registered WebSocket domain errors reuse the same mapping, while
native WebSocket exceptions and unregistered failures retain framework behavior.
Close reasons are limited to 123 ASCII characters. WebSockets are outside
OpenAPI and HTTP client generation. These boundaries follow
[Starlette's exception model](https://starlette.dev/exceptions/).

## Migration from 0.4

- Removing the last domain registry no longer disables normalization. Set
  individual `ErrorOptions` flags to `False` for deliberately native HTTP behavior.
- Omitted type bases now work in compositions; configure a stable public URI
  base before publishing an API if the default URNs are unsuitable.
- `Feature(errors=...)` supplements inferred registries instead of replacing them.
- Invalid OpenAPI now prevents startup. Fix missing definitions and duplicate IDs
  before entering application lifespans.
- Generated clients now see normalized internal failures and complete 422/500
  unions. Regenerate clients from the compiled document.
- Registry reinstallation with changed options and installation after startup
  fail explicitly. Build and install one complete composition per application.
- Broad domain mappings for framework-owned exception classes are rejected when
  the corresponding normalizers are enabled. Use specific domain exceptions.
