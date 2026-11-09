# Generate and check separate clients

Canon exports application contracts and coordinates specialized generators.
Use an external OpenAPI generator such as Orval with your framework's query
integration for HTTP. Canon generates SSE, WebSocket and WebRTC clients.
There is no Canon HTTP client generator or cross-protocol client facade.

## Configuration

Save `fastapi-canon.toml` in the consumer repository:

```toml
[codegen]
version = 2
app = "backend.app:create_app"
contracts = "contracts"

[codegen.targets.streams]
generator = "sse"
language = "typescript"
output = "frontend/src/api/generated-streams"

[codegen.targets.rpc]
generator = "websockets"
language = "typescript"
client-name = "BackendClient"
output = "frontend/src/api/generated-rpc"

[codegen.targets.http]
generator = "external"
generate-command = ["pnpm", "--dir", "frontend", "run", "api:generate"]
check-command = ["pnpm", "--dir", "frontend", "run", "api:check"]
timeout = 120
```

In `pyproject.toml`, prefix tables with `tool.fastapi-canon`, for example
`[tool.fastapi-canon.codegen.targets.streams]`. Paths and external command working
directories are relative to the configuration file. Unknown settings fail.
The app reference accepts an application or synchronous zero-argument factory.
Export never starts its lifespan or resolves application dependencies.

Point Orval's input at `contracts/openapi.json`. Keep its HTTP runtime, query keys,
mutation factories and framework options in its own configuration. External
commands are argument arrays executed without shell interpolation; Canon neither
imports Orval nor manages its output directory. Install Node tooling separately
and pin all generator dependencies in the consumer's lockfiles.

Native targets accept `language` and `output`. RPC and WebRTC targets accept
`client-name`; Python RPC targets also accept `package`. Python SSE targets can set
`python-http-client = "httpx2"`; the default is `httpx`. WebRTC targets support
TypeScript only. Every native output must be a dedicated directory outside the
contracts directory and other native outputs. For additional transport-specific
options, bindings or diagnostic allowances, configure an external command pair
that invokes the existing standalone transport CLI.

## Developer commands

```console
fastapi-canon generate
fastapi-canon generate --check --diff
fastapi-canon generate --target rpc --check
fastapi-canon generate --offline --check
fastapi-canon generate --format json
```

Generation loads the app once, exports OpenAPI/OpenRPC/WebRTC contracts for its
registered routes, and preflights all selected native clients before writing.
Each specialized generator retains its own API and ownership manifest. The
workflow does not merge models, rename operations across protocols or rewrite
generated bindings. It preserves handwritten files and removes stale files only
when the specialized generator's manifest owns them.

Check mode first compares freshly exported contracts with the saved documents.
If they differ, clients are reported as `blocked`: checking them against a stale
saved contract could give a misleading result. Regenerate, review and commit the
contracts and client files together. Absent transports remove stale exported
contract files; remove corresponding targets when removing a transport.

`--diff` implies `--check` and shows native contract/client file differences.
External check commands print their own differences. Canon does not append flags
to those commands. Their check command must be read-only and return 0 when clean,
1 for drift and another nonzero code for an execution/configuration error.
Commands have a configurable finite timeout. External tools own their effects;
generation is not a transaction and does not roll back their partial writes.

`--offline` reads saved contracts without importing the app. It cannot establish
that those contracts still match current server code. Target selection limits
client generation; live contract export remains an application-wide check.

## Reports and exit codes

Exit codes are 0 for success, 1 for drift and 2 for an error. Errors take precedence
over drift. The JSON report has `schema_version = 2`, overall `status`, `check`,
`offline`, `steps` and captured generator diagnostics. Each step names its target,
status, changed paths, native diff and tool output. Contract drift blocks dependent
checks. Invalid configuration or native preflight errors fail before writing;
external failures do not hide completed targets' results.

Application import/factory stdout is excluded from the report. Diagnostic/tool
output is captured separately; external tools remain responsible for their logs.

## Shared types and errors

Reuse Pydantic models and `Error` declarations in Python. The exported schemas
carry the public shapes into each generator. Repeated generated types are
acceptable: the maintained source is the Python contract. Contract tests verify
that the separate generators represent it correctly.

The problem `type` URI identifies an error; `code` and declared public extensions
are available across HTTP, SSE and WebSocket RPC. Separate generated clients have
separate error classes. Use each client's exports or normalize problem payloads
in an application adapter. Do not rely on cross-client `instanceof` identity.
See [shared problems](problems.md) and [the design rationale](sdk-design.md).

## Migration from version 1

Version-1 domain SDK generation is removed. There is no compatibility facade.
Replace `sources`, `identities`, `names`, operation mappings and combined language
targets with the version-2 app/target configuration. Use fresh, separate native
output directories and an external HTTP generator. Review old SDK-owned files
before deleting them; version 2 does not interpret `.canon-codegen.json`.

Update call sites to the specialized client APIs and your HTTP tool's generated
functions/query helpers. Replace facade-specific `.safe()`, connection options,
model imports and error-class checks. RPC/SSE/media connection lifetimes remain
explicit. No wire-protocol migration is required solely for this workflow change.
The server no longer adds SDK-only `x-canon-model-id`/`x-canon-model-name` hints;
standard JSON Schema IDs supplied by applications remain untouched. Update JSON
report consumers from the old SDK-oriented schema to the per-step version 2.

The [shared-error example](../examples/canonical_errors/README.md) demonstrates
separate native clients. Standalone [SSE](integrations/sse/codegen.md),
[WebSocket](integrations/websockets/clients.md) and
[WebRTC](integrations/webrtc/README.md) workflows remain available.
