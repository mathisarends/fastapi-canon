# One SDK for HTTP, SSE and RPC

This runnable example follows the current checkout and its locked dependencies.
Its generated files are reviewable source artifacts, not a separately published
SDK release. The checkout still declares Canon 0.15.0; unreleased changes require
0.16.0 as recorded in the [changelog](../../CHANGELOG.md#unreleased).

## Follow the declarations

[app.py](app.py) declares one Projects feature, a shared ProjectMissing Error and
three operations. [fastapi-canon.toml](fastapi-canon.toml) reads OpenAPI/OpenRPC
from the same composed app and plans both sources into each target.

| Server | Python SDK | TypeScript SDK |
| --- | --- | --- |
| HTTP `get_project` | `await client.projects.get(project_id=1)` | `await client.projects.get({projectId: 1})` |
| SSE `events` | `async for event in client.projects.events(project_id=1)` | `for await (const event of client.projects.events({projectId: 1}))` |
| RPC `cancel` | `await client.projects.cancel(project_id=1)` | `await client.projects.cancel({projectId: 1})` |

`cancel` is an awaited unary RPC call returning None/void. It does not create a
stream/session resource. The managed client opens its WebSocket lazily at `/ws`.
Other RPC operation kinds retain their own
[resource shapes](../../docs/integrations/websockets/clients.md).

Read the committed public [Python client](generated/python/api_client/client.py),
[models](generated/python/api_client/models.py) and
[problem runtime](generated/python/api_client/errors.py), or their
[TypeScript client](generated/typescript/client.ts),
[models](generated/typescript/models.ts) and
[errors](generated/typescript/errors.ts).
The top-level entry points are [Python](generated/python/api_client/__init__.py)
and [TypeScript](generated/typescript/index.ts). Protocol implementations remain
under `internal/`; consumers use the public entry points.

## Generate and verify from the repository root

```console
uv sync --frozen --all-groups
pnpm install --frozen-lockfile --ignore-scripts
uv run --frozen fastapi-canon generate --config examples/canonical_errors/fastapi-canon.toml
uv run --frozen fastapi-canon generate --config examples/canonical_errors/fastapi-canon.toml --check --diff
uv run --frozen fastapi-canon generate --config examples/canonical_errors/fastapi-canon.toml --offline --check --diff
```

Live generation exports the [saved contracts](contracts/) and writes `generated/`.
Offline generation uses those saved contracts. Both check commands compare exact
output without writing files. The `.canon-codegen.json` manifests identify owned
files and source hashes. Edit declarations/configuration or the existing generator
and templates, then regenerate; do not edit or separately format generated code.
The scoped Ruff setting excludes exactly these generated Python artifacts from
independent rewriting. Drift and real consumer tests verify them instead.
The root `clients` gate also checks this example.

## Run both consumers

Start the server in one terminal from the repository root:

```console
uv run --frozen uvicorn examples.canonical_errors.app:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```console
uv run --frozen python examples/canonical_errors/consume.py --base-url http://127.0.0.1:8000
uv run --frozen python scripts/compile_combined_example.py
node examples/canonical_errors/dist/consumer/consume.js http://127.0.0.1:8000
```

The complete consumers are [Python](consume.py) and [TypeScript](consume.ts).
The build helper uses the root TypeScript compiler and scopes its emitted
CommonJS output under the repository's ESM tooling root. It introduces no npm
workspace or additional runtime dependency. Use the root Node 24 toolchain.

Each consumer verifies project 1, its event sequence and a successful cancellation,
then handles the same `project_missing` problem over HTTP, SSE admission, SSE
terminal failure after one event, and RPC. Python receives the shared
`ProjectMissingProblem` model in every case; both languages verify the stable
problem type and `replacement: null` extension. Successful output is:

```json
{"project": 1, "events": [1], "rpc": "cancelled", "problems": ["http", "admission", "terminal", "rpc"]}
```

Python uses `async with ApiClient(...)`; TypeScript closes the client in `finally`.
They consume streams fully and close owned HTTP/WebSocket resources, including on
errors. Stop the foreground server after the demonstration. `dist/consumer` is
disposable build output; `generated/` is the committed generator-owned SDK.

A standalone Python SDK consumer needs Python 3.12–3.14, Pydantic
`>=2.11.2,<3`, HTTPX `>=0.28,<1` and websockets `>=15,<18`; it does not need Canon,
FastAPI or the server package. Expose `generated/python` on the import path (the
runnable script does this explicitly), then import `api_client`. These are this
example's installation constraints, not evidence for every allowed combination.
TypeScript uses standard fetch/WebSocket APIs; the Node 24 demo needs no runtime
npm packages beyond the emitted SDK.

The integration suite checks exact live/offline drift and executes both consumers
against a real local HTTP/SSE/WebSocket server with finite deadlines:

```console
uv run --frozen pytest tests/integration/integrations/test_unified_codegen.py -q
uv run --frozen python scripts/check.py clients
```

For configuration and lifetime options, continue with the
[common generation guide](../../docs/unified-canon-concept.md); for shared server
contracts, see [problems](../../docs/problems.md) and
[stream error phases](../../docs/streams.md).
