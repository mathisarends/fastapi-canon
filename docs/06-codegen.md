# 6. Client generation

Generate typed clients from your app and fail CI when they drift. Install
`fastapi-canon[codegen]` as a dev dependency.

## What gets generated

| Transport | Generator | Languages |
| --- | --- | --- |
| SSE streams | `sse` | Python, TypeScript |
| WebSocket RPC | `websockets` | Python, TypeScript |
| WebRTC sessions | `webrtc` | TypeScript |
| Plain HTTP | your OpenAPI tool (Orval, openapi-ts, …) | any |

Each transport gets its own client, so HTTP tools keep their query and mutation
helpers while stream clients keep their own cancellation rules. Your Python
models and `Error` declarations are the single source.

## Configure

`fastapi-canon.toml` (or `[tool.fastapi-canon.codegen]` in `pyproject.toml`):

```toml
[codegen]
version = 2
app = "backend.app:create_app"
contracts = "contracts"

[codegen.targets.streams]
generator = "sse"
language = "typescript"
output = "frontend/src/api/streams"

[codegen.targets.rpc]
generator = "websockets"
language = "python"
package = "backend_client"
output = "clients/rpc"

[codegen.targets.http]
generator = "external"
generate-command = ["pnpm", "--dir", "frontend", "run", "api:generate"]
check-command = ["pnpm", "--dir", "frontend", "run", "api:check"]
```

Point your HTTP generator at `contracts/openapi.json`. External commands run
without a shell; Canon does not manage their output.
The same file can hold the application's [check settings](07-error-checking.md#several-applications).

## Run

```console
fastapi-canon generate                  # export contracts, write clients
fastapi-canon generate --check --diff   # CI: fail on drift, show the diff
fastapi-canon generate --target rpc     # one target
fastapi-canon generate --offline        # from saved contracts, without importing the app
```

Exit codes: `0` clean, `1` drift, `2` error. Add `--format json` for a machine
report. Commit contracts and clients together.

Generated files are tracked in an ownership manifest: handwritten files next to
them are never touched, and stale generated files are removed.

## Errors in clients

Every client raises `ProblemError` with a subclass per declared error. Classes
from different clients are distinct, so normalize them in an adapter if your app
uses several clients.

## Go deeper

- [SSE options](integrations/sse/02-codegen.md): selecting operations, strict diagnostics
- [WebSocket clients](integrations/websockets/08-clients.md)
- Custom generators: register a `fastapi_canon.codegen.generators.Generator`
  under the `fastapi_canon.generators` entry-point group
- [Contract export API](22-contracts.md)

---

Previous: [5. Composition](05-composition.md) · [Learning path](README.md) · Next: [7. Error checking](07-error-checking.md)
