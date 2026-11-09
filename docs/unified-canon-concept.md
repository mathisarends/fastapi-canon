# Generate one domain SDK

`fastapi-canon generate` combines operations from OpenAPI, OpenRPC and WebRTC
contracts in a common plan, model graph and error runtime.
The generated public API follows domain names; protocol clients live under
`internal/`. See the [server problem guide](problems.md).

## Contents

- [From server declarations to domain methods](#from-server-declarations-to-domain-methods)
- [Configuration](#configuration)
- [Commands and ownership](#commands-and-ownership)
- [JSON generation diagnostics](#json-generation-diagnostics)
- [Consumer API](#consumer-api)
- [Names and shared models](#names-and-shared-models)
- [Connections and supported scope](#connections-and-supported-scope)

## From server declarations to domain methods

The [quickstart](quickstart.md#core-api-routes-errors-and-composition)
builds an HTTP operation, an SSE stream and a WebSocket RPC method in one app,
then generates Python and TypeScript consumers from them.

| Server declaration | Contract source | Generated domain API | Boilerplate handled by the binding |
| --- | --- | --- | --- |
| `@router.get(..., operation_id="projects.get")` | OpenAPI | `client.projects.get(...)` | HTTP request encoding, response decoding and problem handling. |
| `@router.sse(..., operation_id="projects.events")` | The same OpenAPI source | Async iteration over `client.projects.events(...)` | SSE framing, event decoding, completion and terminal problem handling. |
| `RpcChannel("projects").method()` decorating `cancel` | OpenRPC | `client.projects.cancel(...)` | WebSocket connection, request IDs, response correlation and declared errors. |

An SSE endpoint's return annotation supplies the event schema; it does not need
a second handwritten client model. `raises=` automatically registers errors and
describes both HTTP admission errors and terminal stream failures. The
generated iterator yields business events, consumes `canon.complete`, and raises
`ProblemError` for `canon.problem`. EOF without completion is a `TransportError`.
See [stream contracts](streams.md).

RPC exports parameter/result models, errors and session metadata from the service
contract. Its generated methods preserve the declared call, notification,
subscription or session shape. A domain method can use HTTP while its neighboring
method uses RPC; neither needs a transport prefix in the public SDK. Connection,
cancellation and socket/media lifecycles remain explicit where their behavior
matters. See [RPC clients](integrations/websockets/clients.md).

One target plans all selected sources together. Model identities and resolved
problem type URIs let HTTP, SSE and RPC share public DTOs and one error runtime.
Use `--check --diff` in CI to detect contract or generated-file drift. The
[source error checker](error-checking.md) separately
checks supported HTTP, SSE, RPC and WebRTC error mappings and declarations
before export.

## Configuration

Use `fastapi-canon.toml` with a `[codegen]` section. In `pyproject.toml`, prefix
these tables with `tool.fastapi-canon`: `[tool.fastapi-canon.codegen]`,
`[tool.fastapi-canon.codegen.sources.api]`, and so on. The package name scopes
the configuration; `codegen` identifies the SDK generation settings.

For one application, name it with `app` and omit the source tables:

```toml
[codegen]
version = 1
app = "app:create_app"
contracts = "contracts"

[codegen.targets.python]
language = "python"
output = "python/api_client"
package = "api_client"
```

`app` accepts an application or a zero-argument factory. It derives the sources
`http` (OpenAPI), `rpc` (OpenRPC) and `webrtc`, written to
`<contracts>/openapi.json`, `openrpc.json` and `webrtc.json`; `contracts`
defaults to `contracts`. A kind without registered endpoints is skipped and its
stale contract file removed; `--offline` reads the files that exist. Targets
without `sources` use every configured source their language supports; Python
targets leave out WebRTC sources. The source names are the SDK
binding keys, for example `bindings={"rpc": {...}}`.

Use explicit source tables for saved inputs, several applications, name mappings
or operation selection. They can be combined with `app` but cannot reuse a derived
source name:

```toml
[codegen]
version = 1

[codegen.sources.api]
kind = "openapi"
source = "app:app"
output = "contracts/openapi.json"

[codegen.sources.control]
kind = "openrpc"
source = "app:contract"
output = "contracts/openrpc.json"

[codegen.targets.browser]
language = "typescript"
sources = ["api", "control"]
output = "frontend/generated"

[codegen.targets.python]
language = "python"
sources = ["api", "control"]
output = "python/api_client"
package = "api_client"
```

Live sources use `module:attribute`; `factory = true` explicitly calls a factory.
All formats accept the same app or factory reference, including OpenRPC, whose
paths then come from registered routes. The factory is called once per distinct
reference and factory flag in a generation invocation. OpenRPC also accepts the
result of `service.contract(..., app=app)` for standalone services. See
[application export](contracts.md) for the Python API, standalone export CLI and
runtime-secret boundaries. Saved sources use `input = "contracts/api.json"`
instead of `source`/`output`. Kinds are `openapi`, `openrpc`, `webrtc`. Paths are
relative to the config file. Unknown settings fail explicitly.

Optional target settings are `client-name = "ApiClient"` and, for Python,
`http-client = "httpx"` or `"httpx2"`. `package` names the package at the Python
output path; put its parent on the import path. Generation needs Canon's
`codegen` extra. Generated SDKs do not import Canon: Python consumers need
Pydantic, the selected HTTP client, and `websockets` for WebSocket connections.
TypeScript needs fetch/DOM APIs and the ESNext.Disposable type library.

### Incremental adoption from an existing contract

Select operations by their original wire IDs before model planning and binding
generation. This lets a consumer adopt a supported part of an existing API
without editing the exported contract or importing/deploying the application:

```toml
[codegen.sources.api]
kind = "openapi"
input = "contracts/openapi.json"
exclude-operations = ["legacy_admin_operation"]

[codegen.sources.control]
kind = "openrpc"
input = "contracts/openrpc.json"
include-operations = ["projects.cancel", "projects.updates"]
```

`include-operations` is an optional non-empty allowlist; `exclude-operations`
removes explicitly named operations. OpenAPI uses `operationId`, OpenRPC uses
method/notification/subscription/socket names, and WebRTC uses session names.
Selection happens before public `operations` name mappings. Unknown IDs,
duplicate selectors, including and excluding the same ID, or an empty result
fail explicitly. There are no wildcard selectors or automatic exclusions.

Only selected operations and their referenced model closure reach the emitters;
recursive models remain intact. Existing mappings for excluded operations may
stay in the configuration. Saved inputs remain unchanged, and manifests retain
the original input hashes plus the selection settings. Selection changes are
covered by `--check`/`--diff`, ownership and stale-file cleanup.

Unsupported operations still fail generation unless explicitly excluded. For
example, leave a legacy operation on an existing adapter while adopting generated
JSON/SSE/RPC calls, then remove those exclusions when support is available.

## Commands and ownership

```console
fastapi-canon generate
fastapi-canon generate --config fastapi-canon.toml
fastapi-canon generate --config fastapi-canon.toml --check --diff
fastapi-canon generate --config fastapi-canon.toml --offline --check
fastapi-canon generate --config fastapi-canon.toml --target browser
```

Without `--config`, the command reads `fastapi-canon.toml` in the current
directory, then falls back to `pyproject.toml`. Python imports remain under
`fastapi_canon`; command and configuration names use the distribution's hyphens.

Live generation exports contracts before planning. Offline generation reads saved
outputs and never imports application code. `--target` is repeatable. `--diff`
implies read-only checking. Exit codes: 0 success, 1 drift, 2 configuration,
contract or dependency errors. Validate/render all selected targets before writes.

One `.canon-codegen.json` manifest owns each output. Preserve unowned files,
reject unowned collisions, and remove only stale owned files. Outputs cannot
overlap or traverse symlinks/junctions. Use a fresh output when replacing a
specialized SDK; old manifests are not adopted automatically. Generation does
not install dependencies or publish SDKs.

## JSON generation diagnostics

Use `--format json` for common generation and read-only CI checks:

```console
fastapi-canon generate --format json
fastapi-canon generate --format json --check --target browser
fastapi-canon generate --format json --diff --offline
```

Stdout contains one JSON document with `schema_version: 1`. Application stdout
from imports/factories is suppressed in this mode. Structured SSE constraint
warnings are included without copying schema values; specialized generators keep
their existing diagnostics and commands. Text remains the default format.

| Field | Meaning |
| --- | --- |
| `status` | `success`, `drift` or `error`. |
| `changed` | Whether generated artifacts differ; `null` when generation fails. |
| `check`, `offline` | Effective read-only and saved-contract modes; `--diff` implies check. |
| `targets` | Selected configuration target names, or requested names if selection failed. |
| `changes` | Sorted artifact paths for generated changes, including removals. |
| `diagnostics` | Entries with stable `severity`, `code`, `message` and `locations` fields. A clean success can have an empty list. |
| `diff` | Unified diff text when requested, otherwise `null`. |

Locations include `source`, wire `operation`, `target`, `path` and
`schema_pointer` when known. A path identifies an artifact or a method/route such
as `POST /upload`; a schema pointer identifies a constraint within its source.
Global configuration failures can have no location. Invalid/unsupported
operations identify their source and operation where the common planner owns
that information. Imported application exceptions and other unexpected failures
report their type and a corrective hint without their raw contents.

| Code | Severity | Meaning |
| --- | --- | --- |
| `canon.codegen.I001` | info | Generated artifacts were updated. |
| `canon.codegen.W001` | warning | Check/diff found artifact drift. |
| `canon.codegen.W002` | warning | A specialized SSE binding reports a validation constraint limitation. |
| `canon.codegen.E001` | error | Configuration, target loading, contract/planning or artifact ownership failed. |
| `canon.codegen.E002` | error | An operation requests an unsupported binding feature, such as nested multipart encoding. |

`fastapi-canon explain canon.codegen.E002` provides an example and repair for a
generation code; see [diagnostic explanations](diagnostics.md) for text/JSON lookup.

Exit codes remain 0 for successful generation or a clean check, 1 for drift and
2 for invalid configuration/generation. Constraint warnings alone do not change
the exit code. Preflight failures complete before any contracts or SDK files are
written, even when an earlier target planned successfully. Check/diff never
write files. The Python `generate()` API retains its `(changed, report)` return.

For example, a CI step can inspect diagnostics and preserve the command's exit
policy without parsing human-readable messages:

```python
import json
import subprocess

result = subprocess.run(
    ["fastapi-canon", "generate", "--format", "json", "--check"],
    capture_output=True,
    text=True,
    check=False,
    timeout=120,
)
report = json.loads(result.stdout)
assert report["schema_version"] == 1
for diagnostic in report["diagnostics"]:
    print(diagnostic["severity"], diagnostic["code"], diagnostic["locations"])
raise SystemExit(result.returncode)
```

## Consumer API

```typescript
import { createClient, ProblemError } from "./generated";

const client = createClient({ baseUrl: "https://api.example.com" });
try {
  const project = await client.projects.get({ projectId: 1 });
  for await (const event of client.projects.events({ projectId: project.id })) {
    render(event);
  }
  await client.projects.cancel({ projectId: project.id });
} catch (error) {
  if (error instanceof ProblemError) showProblem(error.problem);
  else throw error;
} finally {
  await client.close();
}
```

```python
from api_client import ApiClient, ProblemError

async with ApiClient("https://api.example.com") as client:
    try:
        project = await client.projects.get(project_id=1)
        async for event in client.projects.events(project_id=project.id):
            render(event)
        await client.projects.cancel(project_id=project.id)
    except ProblemError as error:
        show_problem(error.problem)
```

Run the [complete example configuration](../examples/canonical_errors/fastapi-canon.toml)
with `fastapi-canon generate --config examples/canonical_errors/fastapi-canon.toml`.
Its generated output goes into the example's ignored `dist/` directory.

TypeScript output contains `index.ts`, `client.ts`, `models.ts`, `errors.ts`, and
private binding modules under `internal/`. Python has corresponding package
modules. Canonical model declarations and the base error runtime are shared
within this output. Separate SDK outputs still have separate class identities.

Calls return Promises/awaitables, streams expose async iteration, and sessions
return explicit socket/media objects. TypeScript session signatures retain
binding connect options and return types. Python socket/binary sessions use
async context managers. No `sse`/`rpc` prefix is added to domain methods.

## Names and shared models

OpenAPI naming uses configuration, `x-canon-operation`, then the operation ID.
Tags only organize API documentation and never affect SDK names. An unqualified
ID produces a root method. Prefer `APIRouter(namespace="projects")` for stable
domain groups; explicit dotted operation IDs remain supported. JSON-RPC and
WebRTC use declared operation/session names. Override explicitly when adapting
existing contracts:

```toml
[codegen.sources.api.operations]
get_project = "projects.get"
watch_project = "projects.events"

[codegen.sources.control.operations]
"maintenance.cancelProject" = "projects.cancel"
```

Conflicting normalized operation/group names fail. Similar-looking models do not
merge automatically. Assign semantic identity with `x-canon-model-id`, `$id`, or:

```toml
[codegen.sources.api.identities]
Project = "urn:example:project-output:v1"

[codegen.sources.control.identities]
ProjectView = "urn:example:project-output:v1"
```

**TL;DR:** HTTP, SSE and RPC preserve the declared Pydantic model. Give a shared
DTO a semantic identity once; no transport-specific alias or extra-field
workaround is needed:

```python
class Project(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"x-canon-model-id": "urn:example:project-output:v1"},
    )
    project_id: int
```

Import `ConfigDict` and `BaseModel` from Pydantic. This DTO uses `project_id` on
every wire. Pydantic's declared aliases, nested models, validators and `extra`
policy apply unchanged. To choose camel case or reject unknown fields, configure
that on the model; `RpcModel` remains an explicit camel-case, strict convenience
base. TypeScript operation arguments still use camel case (`projectId`), while
DTO properties follow their wire schemas.

**Rationale:** A model defines its contract; a transport adapter should not
silently replace it. A semantic ID identifies a shared model and does not erase
differences between wire schemas. Use separate identities for intentionally
different views, including distinct validation and serialization shapes.

**Migration:** Plain `BaseModel` classes previously received automatic camel-case
aliases and `extra="forbid"` in RPC. Regenerate clients for the declared shape.
To retain the previous wire convention, inherit from `RpcModel` or declare the
equivalent Pydantic configuration explicitly before upgrading the server.

Identical identities require compatible schemas. Different identities with the
same public name require `names = { Project = "ArchivedProject" }` on the relevant
source. Use separate identities for incompatible input/output shapes. Problems
share their type URI automatically, including nested extensions. HTTP status
variants normalize to optional client status; embedded wire problems still omit it.

## Connections and supported scope

Bindings open lazily. `baseUrl` drives HTTP/SSE and an unambiguous single JSON-RPC
endpoint. Override WebSocket connection settings separately:

```typescript
const client = createClient({
  baseUrl: "https://api.example.com",
  bindings: { control: { connect: { url: "wss://control.example.com/ws" } } },
});
```

Multiple servers, template variables and authentication use typed binding
`connect` options. HTTP headers are not forwarded to WebSockets. An injected
`bindings.control.client` stays caller-owned unless `owned: true` is set. Python
accepts `http_client=`, `clients={"control": existing_client}` and
`bindings={"control": {"url": ...}}`; injected Python clients stay caller-owned.
Closing the facade prevents new work and closes the resources it created.
TypeScript `close()` also cancels pending RPC connection initialization, including
an eager handshake selected with `connect: { lazy: false }`. A single call's
timeout or cancellation leaves that shared initialization available to other calls.
The RPC binding's optional `connect.signal` cancels eager connection establishment;
it is combined with the facade's lifetime signal.

HTTP/SSE request options and JSON-RPC call options retain their binding's
semantics. Python uses task cancellation and optional timeouts; TypeScript calls
and SSE support `AbortSignal`. Accepted raw/WebRTC sessions retain their protocol
close/error semantics and do not receive SSE terminal frames.

### Public SDK methods and failures

Common TypeScript SDKs support explicit resource management:

```typescript
import { createClient, ProjectMissingError, type Project } from "./generated/index.js";

await using client = createClient({ baseUrl: "https://api.example.com" });
try {
  const project: Project = await client.projects.get(
    { projectId: 1 },
    { timeoutMs: 5_000 },
  );
} catch (error) {
  if (error instanceof ProjectMissingError) showProblem(error.problem);
  else throw error;
}
```

`timeoutMs` covers the complete HTTP/RPC call, including connection establishment.
RPC `CallOptions` also accepts declared request metadata. HTTP `HttpOptions`
accepts request headers. Authentication for a WebSocket connection remains in
its typed binding options: browser WebSockets cannot set arbitrary HTTP headers.
Calls without parameters accept options directly, for example
`client.system.status({ timeoutMs: 1_000 })`. Deadlines raise native
`TimeoutError` DOMExceptions; caller cancellation preserves its abort reason.

SSE `StreamOptions` has `openTimeoutMs` until response headers arrive and
`idleTimeoutMs` while waiting for the next business event. Heartbeats and ignored
unknown events do not reset that idle deadline; time spent processing a yielded
event does not count. Both limits are optional. Cancellation preserves the caller's
abort reason; a stream deadline raises a native `TimeoutError` DOMException.

Each declared canonical problem has one public error class shared by HTTP, SSE
and RPC. Its payload contains only the server's public contract. Unknown problem
type URIs remain generic `ProblemError` instances, even if their code is familiar.
The root exports reachable domain models and problem models; protocol envelopes
and generated implementation aliases stay in the internal model module.

Each TypeScript call also has a typed `.safe()` method:

```typescript
const result = await client.projects.get.safe({ projectId: 1 });
if (result.ok) render(result.data);
else if (result.kind === "declared") showProblem(result.problem);
else showUnexpectedProblem(result.problem);
```

The declared branch contains the operation's problem union, including documented
framework problems. The unexpected branch preserves an unknown public problem.
Transport failures, invalid protocol responses, non-problem HTTP errors, deadlines
and cancellation still throw. Streams report problems during iteration and do
not have a `.safe()` method. TypeScript retains static payload types and standard
problem-member checks; it does not add full runtime validation of extension fields.

The public `HttpError` exposes `status`, `details` and `response` for deliberate
inspection of non-problem HTTP failures. Its message contains only the status;
TypeScript body and response properties are non-enumerable. SDKs never log the
body or headers automatically. Public error payloads do not include server
exception messages or internal causes unless the application explicitly publishes
such fields.

Python exports the same canonical error classes and `HttpError`. An omitted
`RequestOptions.timeout` inherits the injected HTTP client's configuration;
explicit `None` disables its timeout. An explicit RPC timeout includes lazy
binding initialization. Generated package imports remain valid when the package
is renamed or embedded inside another package.
Closing a Python client cancels its owned pending binding initialization,
including stalled eager WebSocket handshakes. Cancellation of one call still
leaves shared binding initialization available to other calls.
The Python constructor accepts the selected HTTP library's `AsyncClient` and
generated `Bindings`/`Clients` dictionaries. Each RPC source exposes a typed
connection-options class, derived from that binding's real `connect()` signature:

```python
from api_client import ApiClient, ControlConnectionOptions

connection: ControlConnectionOptions = {"reconnect": True, "retry_attempts": 2}
async with ApiClient(base_url, bindings={"control": connection}) as client:
    await client.projects.cancel(project_id=1)
```

Update broadly typed wrapper dictionaries to the generated option types when
migrating. Option/source misspellings are now static errors; supported runtime
options and ownership rules retain their behavior.
Streams using a facade-owned Python HTTP client retain their unlimited read
timeout; injected clients keep their own timeout unless explicitly overridden.

TypeScript output supports browser bundlers and Node ESM (`NodeNext` resolution),
using explicit `.js` module paths. Import the generated `index.js` entry point.
Generated TypeScript also supports `verbatimModuleSyntax`,
`exactOptionalPropertyTypes`, `noUnusedLocals` and `noUnusedParameters`.
Transport options accept explicit `undefined`; omitted optional operation
arguments stay omitted on the wire. Type-only re-exports do not create runtime
imports. Python output is formatted and its imports are cleaned with the Ruff
version installed by `fastapi-canon[codegen]`.

Operation namespaces are normalized once before grouping. For example,
`projects.get` and `Projects.list` become `client.projects.get()` and
`client.projects.list()` in both languages. Collection signatures retain the
canonical schema type rather than approximating nested maps or enums as JSON.
Human-readable output summarizes validation constraint limitations; use
`--format json` to inspect each source-located diagnostic.

Unified HTTP generation supports JSON bodies/responses, scalar path/header
parameters, exploded scalar-array query parameters, flat multipart uploads and
binary transfers described below. Cookies, other parameter styles and unsupported
schema constructs fail preflight.
Specialized generators remain available for their additional binding options
and use separate output ownership. WebRTC targets TypeScript; requesting Python
fails before writing.

Common DTOs use JSON wire scalar types: UUID/date-time values are strings in
both language targets, including RPC primitive results. Python names use snake
case and TypeScript operation arguments use camel case; binding wire names stay
explicit. Python uses Pydantic validation. TypeScript models are static types, with runtime
checks for standard problem members and SSE controls, not a full JSON Schema
validator. Common generation provides [JSON diagnostics](#json-generation-diagnostics).
Replay and automatic manifest migration remain future work.

Python string constraints use Pydantic's default regex engine where supported,
with compiled Python patterns for lookarounds and backreferences. This includes
decimal-string schemas exported by earlier Pydantic versions. Constraints stay
active in model fields, nested arrays and standalone type aliases; invalid
patterns fail generation. Decimal wire values stay strings, preserving precision.

HTTP clients omit optional query/header parameters whose value is null. Required
query/header parameters need a non-null value; a nullable JSON body still sends
JSON `null`.
Python validates each response against the schema for its actual status. An
exact status declaration takes precedence over `2XX` in both language targets.

TypeScript HTTP methods distinguish bodyless responses (`void`/`undefined`) from
JSON `null` (`null`). Operations supporting both JSON and bodyless success
statuses return the JSON type or `void`. Empty or malformed JSON and unexpected
bodies for bodyless contracts raise `ProtocolError`.
Python also rejects empty JSON and unexpected bodies for bodyless contracts.
HTTP connection failures, including interrupted response bodies and problem
responses, raise `TransportError`. TypeScript retains native abort errors for
explicit cancellation and request timeouts.

### Multipart uploads and binary transfers

Generate file operations from the same application as JSON, SSE and RPC methods.
FastAPI `UploadFile`/`File` inputs and `Form` fields produce the contract; no
separate HTTP binding is needed for the supported cases. The common reader
recognizes OpenAPI `format: binary` and FastAPI's unencoded
`contentMediaType: application/octet-stream` string schemas.

Install `fastapi-canon[uploads]` on the server for the multipart parser.
Single `file: UploadFile` and repeated `files: list[UploadFile]` parameters work
directly. Use `Annotated[..., File(...)]` for aliases or descriptions and
`Annotated[T, Form()]` for scalar form fields. See the [file transfer guide](integrations/uploads.md)
for the complete developer API and runnable upload/download example.

Multipart request bodies must be flat objects with named parts. Parts
may be files, strings, numbers, integers or booleans, including arrays of each.
Arrays become repeated parts with the same wire name, preserving order. Optional
parts can be omitted; `None`/`null` also omits an optional part. Empty arrays emit
no parts, so a server requiring at least one value can reject an empty list.
An entirely optional form can be omitted. Nested objects/arrays, encoded content,
conditional required parts in an optional body, custom per-part headers and non-default serialization fail
before contract or SDK files are written. Explicit encoding may use `style =
"form"`, `explode = true`, and `text/plain` for scalars or
`application/octet-stream` for files. File values supply their actual MIME type.

For an operation with a `document` file, repeated `attachments` files and a
`title` form field, the generated consumer API is:

```python
from api_client import ApiClient, Upload

async with ApiClient(base_url) as client:
    with open("report.pdf", "rb") as report:
        result = await client.files.upload(
            document=Upload("report.pdf", report, "application/pdf"),
            attachments=[Upload("notes.txt", b"Notes", "text/plain")],
            title="Quarterly report",
        )
```

```typescript
const result = await client.files.upload({
  document: reportFile, // Native File, preserving its name and MIME type.
  attachments: [new File(["Notes"], "notes.txt", { type: "text/plain" })],
  title: "Quarterly report",
});
```

Python `Upload.content` accepts bytes or a binary file handle. The HTTP library
streams handles through its multipart encoder and may rewind seekable handles;
the caller keeps them open until the await completes and closes them afterwards.
Success, canonical errors, cancellation and SDK shutdown never close borrowed
handles. TypeScript accepts `File` or `Blob`; use `File` to specify a filename,
since plain `Blob` uses the platform's default filename. The HTTP library/fetch
constructs the multipart boundary and overrides any configured `Content-Type`.

Raw `application/octet-stream` request bodies accept Python `bytes` or TypeScript
`Blob`. Successful responses with one media type and an explicit binary schema
return `bytes` or `Blob`, including empty files. A TypeScript result retains its
response MIME type. Downloads are buffered in memory; this API does not expose
a download stream or upload progress. Multipart Python file handles avoid
buffering the whole upload in the SDK. `RequestOptions`/`HttpOptions`, declared
canonical errors and TypeScript `.safe()` work as for JSON HTTP methods.
Cancellation and client shutdown abort active HTTP work; interrupted binary
response bodies raise `TransportError`.
