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
TypeScript under `dist/generated/typescript` and the Python package under
`dist/generated/python/api_client`. The quickstart shows how to call them.
Do not edit generated files; change the Python declarations and regenerate.
