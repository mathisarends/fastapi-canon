# Export application contracts

Compose the application once and export the routes registered on that application.
HTTP and SSE share OpenAPI; JSON-RPC, binary streams and raw sockets use OpenRPC;
WebRTC sessions retain their session contract. Router declarations describe the
messages and operations, while registered routes determine their effective paths.

## Python API

```python
from fastapi_canon import Contracts, export_contracts

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

Select formats with `export_contracts(app, kinds=("openapi",))`. The Python API
uses FastAPI/Pydantic contract generation; the CLI and SDK pipeline additionally
validate OpenAPI against its meta-schema and JSON Schema. Those commands require
`fastapi-canon[codegen]`.

RPC URLs default to `{baseUrl}` followed by the complete registered path. Use
`base_url="https://api.example.com"` to publish a concrete origin; HTTP(S) origins
are converted to WS(S). A public origin may include a proxy's stripped prefix.
Do not repeat a prefix already registered through `include_router` or `mount`.
Request-specific ASGI `root_path` values cannot be discovered by an offline export.

## CLI

```console
fastapi-canon contracts export --app backend.application:create_app --factory --output-dir contracts
fastapi-canon contracts export --app backend.application:create_app --factory --output-dir contracts --check --diff
fastapi-canon contracts export --app backend.application:app --kind openapi --output-dir contracts
```

`--factory` explicitly invokes a zero-argument app factory. Without it, the reference
must resolve to a FastAPI instance. `--kind` is repeatable. All three formats are
selected by default, and absent RPC/WebRTC transports produce no file. Explicitly
selecting a missing transport is an error. `--base-url` supplies the public RPC origin.

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

RPC endpoint and operation names must be unique across the export, and protocol
versions must agree. The current SDK binds each RPC operation to one server.
Registering the same endpoint under multiple paths is therefore rejected with
both paths; export separate applications or give distinct operations and endpoints
explicit names. Undeclared ordinary WebSockets are outside the typed contract.

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

## SDK generation

Use the same app reference for each live source in `fastapi-canon.toml`:

```toml
[codegen.sources.api]
kind = "openapi"
source = "backend.application:create_app"
factory = true
output = "contracts/openapi.json"

[codegen.sources.control]
kind = "openrpc"
source = "backend.application:create_app"
factory = true
output = "contracts/openrpc.json"
```

Keep the existing `[codegen]` version and target tables. One generation invocation
calls each distinct `(source, factory)` once, sharing that app across formats and
targets. Application-derived RPC bindings inherit the domain client's base URL;
connection options can override it and supply resource IDs. `generate --offline`
reads saved contracts and never imports the app. Standalone `RpcContract` sources
remain supported for framework-independent services.
