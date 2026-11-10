# Projects: Python contracts to clients

This small application is the runnable counterpart to the
[quickstart](../../docs/quickstart.md). It demonstrates contracts, not a
background task scheduler:

- `GET /projects/1` returns a typed project; project `0` raises a public problem.
- `GET /projects/1/events` emits an event and completes; project `2` demonstrates
  a terminal stream problem. Project `0` fails admission before the stream starts.
- `/ws` exposes `projects.cancel`, reusing the same domain error.

From the repository root, start the server:

```console
uv run --frozen uvicorn examples.canonical_errors.app:app --reload
```

In another terminal, change into this directory and generate clients:

```console
cd examples/canonical_errors
uv run fastapi-canon generate
uv run fastapi-canon generate --check --diff
```

The local `fastapi-canon.toml` exports OpenAPI and OpenRPC, then produces
separate TypeScript SSE/RPC clients under `dist/streams/typescript` and
`dist/rpc/typescript`, plus Python clients under `dist/streams/python` and
`dist/rpc/python/projects_client`. Use an external OpenAPI generator for HTTP.
See the [workflow](../../docs/codegen.md) and
[design rationale](../../docs/sdk-design.md). Shared errors come from the same
Python declarations; separate generated classes have separate runtime identities.
Do not edit generated files; change the Python declarations and regenerate.

## Replace a version-1 SDK consumer

In Canon 0.18.0 and later, the configuration selects `projects.events`
and uses strict SSE diagnostics. The checked-in allowances accept only reviewed
`uri-reference` format limitations. Reports go under `dist/reports`; new diagnostics
fail generation until reviewed. TypeScript event types do not validate incoming
schemas at runtime, and Python does not enforce every JSON Schema format.

After generation, run the Python consumer from the repository root:

```console
uv run --frozen python -m examples.canonical_errors.consumer --project-id 1
uv run --frozen python -m examples.canonical_errors.consumer --project-id 0
uv run --frozen python -m examples.canonical_errors.consumer --project-id 2
```

The first command returns `[1]`. The others expose the same public problem at
HTTP admission and after a stream event. `consumer.py` borrows an HTTPX2 pool,
closes an early-exited iterator with `aclosing`, and leaves pool shutdown to its
caller. Application pools can serve other requests after the stream client closes.

`consumer.ts` contains the browser adapters. Compile them with:

```console
pnpm exec tsc -p examples/canonical_errors/tsconfig.json
```

Import `readEvents`, `problemOf` and `openProjectsRpc` into your application.
`problemOf` recognizes each generator's own error class and returns the public
problem payload; use its `type` URI to identify an error across clients. SSE
admission and terminal failures share this adapter. Unknown errors remain unknown.
The functions use the specialized APIs directly, without `.safe()` or a combined
model facade. Keep your HTTP generator and its query/mutation integration.

Each `openProjectsRpc` call returns a separately owned connection. Close it in
`finally`. Pass your application's `refreshSession` function: browsers hide a
failed handshake's status, so initial admission is retried once after renewal.
The sample server is public and has no session-refresh endpoint. Cancellation
prevents renewal/retry. The default connection does not reconnect or replay calls;
use `shared: true` only for an application-owned connection that may reconnect.
A `1008 / credentials_expired` close renews the session before that reconnect.
Keep terminal, speech and browser-control sessions separately owned when replay
or reconnection would be incorrect. Close and replace shared connections when the
signed-in user changes; credentials do not change an existing connection's identity.

See [version-1 migration](../../docs/codegen.md#migration-from-version-1) for the
configuration and file-ownership changes.
