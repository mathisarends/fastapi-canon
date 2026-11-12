# Export application contracts

Compose the application once and export the routes registered on that application.
HTTP and SSE share OpenAPI; JSON-RPC, binary streams and raw sockets use OpenRPC;
WebRTC sessions retain their session contract. Router declarations describe the
messages and operations, while registered routes determine their effective paths.

## Python API

The typed selection API below is unreleased and requires the next minor release
(0.20.0). Canon 0.19 uses string format names and has no `selection` argument.

```python
from fastapi_canon import export_contracts
from fastapi_canon.contracts import ContractKind, Contracts

app = create_app()
contracts: Contracts = export_contracts(app)

openapi = contracts.openapi
openrpc = contracts.openrpc
webrtc = contracts.webrtc
```

`Contracts` is a snapshot, independent of the application's cached OpenAPI document.
Each attribute is a JSON document or `None`. Unregistered RPC/WebRTC transports
and unselected formats are `None`; HTTP/SSE always has an OpenAPI document when
selected. `contracts.documents` maps the present names to their documents.

Select formats with `export_contracts(app, kinds=(ContractKind.OPENAPI,))`.
`ContractKind` is a `StrEnum` with `OPENAPI`, `OPENRPC` and `WEBRTC` members;
raw Python strings are rejected. CLI format names remain unchanged. The Python API
uses FastAPI/Pydantic contract generation; the CLI and SDK pipeline additionally
validate OpenAPI against its meta-schema and JSON Schema. Those commands require
`fastapi-canon[codegen]`.

RPC URLs default to `{baseUrl}` followed by the complete registered path. Use
`base_url="https://api.example.com"` to publish a concrete origin; HTTP(S) origins
are converted to WS(S). A public origin may include a proxy's stripped prefix.
Do not repeat a prefix already registered through `include_router` or `mount`.
Request-specific ASGI `root_path` values cannot be discovered by an offline export.

### Export an installed feature

Reuse the feature object installed in the application:

```python
from fastapi import FastAPI

from fastapi_canon import APIRouter, Composition, Feature, export_contracts
from fastapi_canon.contracts import ContractKind, ContractSelection

router = APIRouter()
control = router.channel("/rpc", namespace="browser")


@control.method()
async def ping() -> str:
    return "pong"


browser_tunnel = Feature(name="browser_tunnel", routers=(router,))
app = Composition(features=(browser_tunnel,)).build(FastAPI())
contracts = export_contracts(
    app,
    kinds=(ContractKind.OPENRPC,),
    selection=ContractSelection(features=(browser_tunnel,)),
)
```

`ContractSelection` is immutable. `features` accepts actual `Feature` references,
not their names. An uninstalled object is rejected even if its name and routers
match an installed feature. Keep factory-created references with their app rather
than constructing another feature just for export. No providers or lifespans run
during export.

Feature ownership is recorded when composition installs routes. Later changes to
a feature's router do not acquire ownership of additional routes. Selection reads
the effective registered routes, including composition and mount prefixes.

Without composition, or to narrow repeated mounts, select exact path templates:

```python
selection = ContractSelection(paths=("/api/projects/{project_id}/rpc",))
```

Entries in each field use OR; `features` and `paths` together use AND. Empty lists,
duplicate references/paths, unmatched paths and selections without documented
routes for the requested formats fail. Omit `selection` for a full export.
A feature selects every installed occurrence, including mounted subapplications;
combine it with `paths` to choose one occurrence. Selecting individual RPC methods
is not supported: a registered endpoint retains its methods, events and streams.

RPC and WebRTC selection happens before their aggregate contract validation.
Separate selections can reuse names or have different RPC service versions;
combining incompatible routes still fails. OpenAPI selection preserves each
selected route's methods, metadata and reachable model schemas without changing
the application's cached document or runtime routes. Composition validation and
canonical error identities remain application-wide.

HTTP selection retains FastAPI's OpenAPI boundary. Export a mounted application
directly for its HTTP document; selecting its prefixed HTTP path on the parent
reports that separate boundary. RPC/WebRTC mount paths remain selectable. Apps
with independent error catalogs must still be exported separately.

## CLI

```console
fastapi-canon contracts export --app backend.application:create_app --output-dir contracts
fastapi-canon contracts export --app backend.application:create_app --output-dir contracts --check --diff
fastapi-canon contracts export --app backend.application:app --kind openapi --output-dir contracts
```

### Application target rules

`--app` accepts an existing FastAPI instance or a synchronous zero-argument factory
returning one. Instances are used directly; factories are called automatically,
without an additional flag. Async factories are rejected. `--kind` is repeatable.
All three formats are selected by default, and absent RPC/WebRTC transports produce
no file. Explicitly
selecting a missing transport is an error. `--base-url` supplies the public RPC origin.

References accept dotted attributes such as `backend.application:bootstrap.app`
or `backend.application:bootstrap.create_app`. Imports and synchronous construction
execute normally; loading never enters lifespan or resolves runtime dependencies.

The selected filenames `openapi.json`, `openrpc.json` and `webrtc.json` belong to
the export directory. Default export removes a stale selected file when its last
transport is unregistered. Unselected formats and other filenames are preserved.
Documents use sorted keys, UTF-8 and LF endings. Export and serialization complete
before writes begin; symlink outputs are rejected. Exit codes are 0 for success,
1 for drift in check mode and 2 for configuration/export errors. `--diff` implies
`--check`; both leave files unchanged.

## Router colocation

```python
from fastapi import APIRouter, FastAPI

tunnel = APIRouter(prefix="/projects/{projectId}/browser-tunnel/sessions/{sessionId}")
tunnel.include_router(control_routes)
tunnel.include_router(screencast_routes)
tunnel.include_router(frames_routes)

app = FastAPI(title="Backend Browser Tunnel")
app.include_router(tunnel, prefix="/api/v1")
```

No separate endpoint URL map is needed. Only registered RPC endpoints contribute
operations and schemas. Custom `serve=` handlers preserve their endpoint metadata.
Nested router prefixes and mounted subapplication paths are included.
HTTP/SSE retains FastAPI's OpenAPI boundary: a mounted subapplication has its own
OpenAPI document. Export applications with independent error catalogs separately;
the combined RPC export uses the selected application's installed catalog.

RPC endpoint and operation names must be unique within the export selection, and
protocol versions must agree. The current SDK binds each RPC operation to one server.
Registering the same endpoint under multiple paths is therefore rejected with
both paths; select one registered occurrence or give distinct operations and
endpoints explicit names. Undeclared ordinary WebSockets are outside the typed
contract.

`projectId` and `sessionId` remain unresolved URL variables, without fabricated
example IDs. With the current RPC SDK they belong to connection options; they are
not inserted into each RPC method's business parameters. A future resource-bound
client API can build on these contracts independently.

## Export without runtime secrets

Export calls no route, dependency, provider function or lifespan. Module imports
and app construction still run normally. A CLI cannot undo eager settings loading:

```python
from dishka import Provider, Scope, provide


class AuthenticationProvider(Provider):
    @provide(scope=Scope.APP)
    def settings(self) -> JwtSettings:
        return JwtSettings()
```

Constructing this provider does not construct `JwtSettings`. Resolve and validate
required settings during normal application startup, then acquire databases and
other resources in their owning runtime scopes. Keep the same router registrations,
dependencies, auth declarations and errors in the exported app.

Prefer a side-effect-free factory module such as `backend.application:create_app`.
Importing `backend.main:create_app` still executes a module-level `app = create_app()`
if one exists there. Contract-affecting configuration, such as feature selection
or error type bases, must remain explicit and equal to the deployed configuration.
Do not inject placeholder secrets or silently disable authentication for export.

## Client generation

The configured version-2 workflow still exports application-wide contracts;
Python `ContractSelection` does not add selectors to its TOML targets. Use the
[current generation workflow](codegen.md) for separate clients and external HTTP
tools. The remaining target-selection and staging proposal is tracked in
[issue #154](https://github.com/mathisarends/fastapi-canon/issues/154).
