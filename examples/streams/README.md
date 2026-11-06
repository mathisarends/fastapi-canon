# Typed streams with Canon errors

A runnable SSE feature: authenticated GET and POST streams of a discriminated
event union, declared with `APIRouter.sse`. See [`docs/streams.md`](../../docs/streams.md)
for the complete API.

```console
uv run --with uvicorn uvicorn examples.streams.app:app
curl -N -H "Authorization: Bearer demo" http://127.0.0.1:8000/runs/1/events
```

Omit the header for a `401` problem, use `Bearer forbidden` for a normalized
`403`, or request run `0` for a `404` problem. All of them are returned before the
stream starts.

## What the example shows

- `APIRouter(prefix="/runs", raises=[...])` shares
  authentication and error contracts between both stream operations.
- `@router.sse(..., methods=[HTTPMethod.POST])` accepts a typed JSON body.
- Resource checks run in dependencies, so they fail as HTTP problems before
  the response starts. Generator code always runs after the response starts.
- The generator opens a short `ServiceScope` to materialize its events instead
  of keeping a request-scoped service alive for the whole stream.
- `Feature` collects the router's error registry; `Composition` installs the
  problem handlers, OpenAPI compiler and Dishka container.

## Contracts and clients

Export the OpenAPI document, which contains the typed SSE events and problem
schemas:

```console
uv run --locked fastapi-canon contracts export --app examples.streams.app:app --output-dir contracts
```

Use [`fastapi-canon generate`](../../docs/unified-canon-concept.md) to generate a
Python or TypeScript SDK whose stream methods are async iterators.

## Tests

```console
uv run --locked pytest tests/integration/integrations/test_streams.py
```
