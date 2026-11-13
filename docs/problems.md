# Canonical errors

Define a domain exception once, map its public problem once, and declare that
same `Error` on HTTP, SSE, and RPC handlers of one `APIRouter`. Generated
Python and TypeScript clients raise the same problem types. The complete executable example is in
[`examples/canonical_errors/app.py`](../examples/canonical_errors/app.py).

## Define the public error

```python
from fastapi_canon import Error


class ProjectMissing(Exception):
    pass


project_missing = Error(
    ProjectMissing,
    status=404,
    code="project_missing",
    title="Project not found",
    detail="Choose another project.",
    extensions={"replacement": None},
)
```

The exception stays framework-free. Its exception text is private by default;
only the declared detail and extension renderers produce public data. For typed
dynamic extension fields, annotate the renderer's return type with a Pydantic
model; Canon uses it as `extensions_model`:

```python
class RetryFields(BaseModel):
    retry_after: int


def retry_fields(error: RateLimited) -> RetryFields:
    return RetryFields(retry_after=error.seconds)


rate_limited = Error(
    RateLimited,
    status=429,
    code="rate_limited",
    title="Too many requests",
    extensions=retry_fields,
)
```

Pass `extensions_model=` explicitly for a lambda or to document a wider model
than the renderer returns. Explicit null extension values are preserved.

Static `headers=` document themselves in OpenAPI as constant strings, so
`headers={"WWW-Authenticate": "Bearer"}` needs no second declaration. Use
`openapi_headers=` to refine a header's description or schema, matched by
case-insensitive name, and to document headers rendered by a `headers` callable.

[RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html) makes `type` the primary
problem identity. Canon's emission profile requires `type`, `title`, and its
`code` extension. These additional profile requirements do not come from the RFC.
Titles describe the type; public details describe an occurrence. Consumers must
not parse either string for control flow.

## Declare errors where an operation can fail

```python
from collections.abc import AsyncIterator

from fastapi import Depends
from pydantic import BaseModel

from fastapi_canon import APIRouter
from fastapi_canon.integrations.websockets import RpcChannel

router = APIRouter()
projects = RpcChannel("projects")


class ProjectEvent(BaseModel):
    sequence: int


class CancelProject(BaseModel):
    project_id: int


def require_project(project_id: int) -> None:
    if project_id == 0:
        raise ProjectMissing("private database information")


@router.get("/projects/{project_id}", raises=[project_missing])
async def get_project(project_id: int) -> dict[str, int]:
    require_project(project_id)
    return {"id": project_id}


@router.sse(
    "/projects/{project_id}/events",
    operation_id="events",
    tags=["projects"],
    raises=[project_missing],
    dependencies=[Depends(require_project)],
)
async def events(project_id: int) -> AsyncIterator[ProjectEvent]:
    yield ProjectEvent(sequence=1)
    if project_id == 2:
        raise ProjectMissing("private database information")


@projects.method(raises=[project_missing])
async def cancel(params: CancelProject) -> None:
    require_project(params.project_id)
```

Each `raises=` declaration automatically registers its `Error` instances. For SSE,
the same declaration covers HTTP admission failures from dependencies and terminal
processing failures. A generator's body runs
after streaming starts, including the code before its first yield. Put admission
checks in dependencies. Sync and async generators use the same terminal profile.

Application domain errors use the shared `Error` definitions above; there is no
second domain-to-RPC mapping to maintain. `RpcError` is only for contracts that
exist solely in the JSON-RPC protocol.

## Compose the application and export its contracts

```python
from fastapi import FastAPI

from fastapi_canon import Composition, ErrorOptions, Feature, export_contracts

router.include_channel(projects, path="/ws")

app = Composition(
    features=(Feature(name="projects", routers=[router]),),
    errors=ErrorOptions(type_base="https://api.example.com/problems"),
).build(FastAPI(title="Projects"))

contracts = export_contracts(app, base_url="wss://api.example.com")
```

The router contributes the errors declared by its HTTP, SSE and RPC operations.
Composition merges definitions by identity, checks conflicts and installs the
resolved application catalog. Configure the common URI prefix through
`ErrorOptions(type_base=...)`; an explicit `Error.type` keeps its own URI.
Use `Feature(errors=[...])` only for additional errors that are not declared
anywhere, such as dependency failures. Registration
alone does not add those errors to an operation's documented contract.
`export_contracts` reads the composed app, so `contracts.openapi` and
`contracts.openrpc` use the same resolved type URIs as the runtime responses.

Use the [generation workflow](codegen.md) for separate clients. Reuse comes
from Python declarations and public problem contracts. Generated classes have
separate runtime identities.

## What goes over the wire

| Occurrence | Binding | HTTP status in the problem |
| --- | --- | --- |
| HTTP or SSE admission failure | `application/problem+json` response | Actual response status |
| Error during SSE processing | terminal `event: canon.problem` | Omitted |
| Declared RPC domain failure | JSON-RPC `error.data.problem` | Omitted |

The JSON-RPC numeric error code and message remain in the normal envelope.
Canonical application errors use numeric server code `-32000`; the problem's
`type` distinguishes the application problem. Its extensions stay at the problem
root. The embedding is a Canon protocol binding of the RFC data model, rather
than a claim that RFC 9457 standardizes JSON-RPC or SSE.

Every normally exhausted Canon stream sends `canon.complete` with `{}`. A terminal
problem closes the stream without a completion frame. Undeclared processing
errors and failed public renderers are logged and produce a safe internal
problem. Cancellation and disconnect propagate through generator cleanup.
`canon.*` event names are reserved for the profile. Binary RPC streams, raw socket
messages, and WebRTC media do not receive SSE control frames.

The SSE item schema describes business, problem, and completion frames. The
contract advertises version 1 in `x-canon-stream`, including its terminal problem
schemas and the business payload schema consumed by code generation. HTTP problem unions discriminate on resolved `type`.
The OpenRPC contract identifies canonical schemas with `x-canon-problem-schema`
and their stable URI with `x-canon-problem-type`.

## Handle failures in generated clients

The generated SDKs export `ProblemError`, `TransportError`, and `ProtocolError`.
`ProblemError.problem` carries public problem data and preserves unknown
extensions. Common SDKs expose public generated error subclasses for known
canonical problems, such as `ProjectMissingError`, shared by HTTP, SSE and RPC.
These classes improve consumer typing without adding public payload fields.
Python RPC dispatch tables in `errors.py` are private implementation details,
not extension points. Declare errors in the service contract and regenerate the
SDK; catch the exported error classes instead of modifying generated registries.
Python stream problems are validated against the operation's declared models.
TypeScript follows the generator's existing static model validation policy;
standard problem members and stream control messages are checked at runtime.

The [source checker](error-checking.md) can additionally find missing
registry mappings (`CAN002`) and `raises=` entries (`CAN001`) along supported
explicit source paths. Run `fastapi-canon check errors src --strict` in CI;
add `--require-complete` to fail for unresolved paths or an empty supported scan.
It reports incomplete analysis as `CAN003` and covers HTTP operations, SSE
streams, WebRTC sessions and RPC methods. Runtime mapping, document validation and source analysis cover distinct
parts of the contract.

SSE clients consume completion frames and expose terminal problems. RPC clients
unwrap problems from the JSON-RPC envelope. Unknown type URIs remain generic
problems; a familiar `code` alone does not make them a known subclass.
Separate clients have separate error classes. Use each client's exports or
normalize public problem payloads in an application adapter. External HTTP
tooling owns its HTTP error handling.


## Custom transport adapters

In the prepared 0.21.0 release, adapters can read the application's installed
`ErrorCatalog` through `fastapi_canon.error.installed_catalog(app)`. The protocol
provides read-only error lookup and resolved problem type URIs; `AnyError` is the
shared type for an error definition with any exception type.

Use `registry_scope(catalog)` while rendering transport contracts or problems.
`resolved_registry(fallback)` selects that context's catalog and otherwise uses
the supplied fallback. Scopes restore the previous catalog on exit, including
exceptions; passing `None` selects the fallback within that scope. Read the
catalog through this API rather than depending on application-state attributes.

## Migration

### Titles are public text

**TL;DR:** `title` remains a required public string. Generated schemas no longer
constrain it to one literal, for domain, validation, generic HTTP and internal
problems. `Error(title=...)` still supplies the server's public default text.

**Rationale:** A wording correction or localized title should not turn a known
problem into a client protocol failure. Problem identity belongs to its resolved
`type` URI. The `code`, HTTP status and declared extension constraints remain
unchanged. This change adds no exception messages, internal causes or diagnostic
fields to the public error payload.

Regenerate and distribute clients before changing server titles: older Python
clients still enforce their generated title literal. Replace comparisons or
discriminators based on title text with the generated error class or problem
type. Explicit examples may use a different valid title from `Error.title`.

Regenerate Python and TypeScript clients after upgrading. Catch `ProblemError`
for HTTP problem responses instead of inspecting `HttpError.details`;
`HttpError` remains the fallback for other HTTP error bodies. Update manual SSE
consumers to handle the two terminal events. Update HTTP schema consumers that
used the old `code` discriminator to use `type`. Give business-specific problems
distinct stable type URIs; ambiguous `about:blank` unions fail compilation.
