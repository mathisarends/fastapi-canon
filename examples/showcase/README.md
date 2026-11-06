# fastapi-canon showcase

Follow the [feature-to-client guide](../../docs/getting-started.md) for a short,
tested path through `APIRouter`, `Inject`, composition, checks and the generated
SDK. This directory is its runnable source.

This small application demonstrates the recommended application boundary:
each feature owns its router and its domain-error contracts, while `app.py`
is the only composition root. Its layout is deliberately suitable as a starting
point for a real service:

```text
showcase/
  app.py                         # composition root
  features/
    catalog/
      __init__.py                # public `feature` export
      router.py                  # APIRouter HTTP layer
      models.py                  # request and response models
      exceptions.py              # domain exceptions, without HTTP concerns
      errors.py                  # Error / ErrorRegistry contracts
      service.py                 # framework-independent use cases
      providers.py               # Dishka bindings contributed by the feature
    status/
      __init__.py
      router.py                  # APIRouter health endpoint
```

Run it from the repository root:

```console
uv run --frozen uvicorn examples.showcase.app:app --reload
```

Then open [the API documentation](http://127.0.0.1:8000/docs), or try the
following requests:

```console
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/products/coffee
curl http://127.0.0.1:8000/products/missing
curl -X POST http://127.0.0.1:8000/products/tea/reservations -H "content-type: application/json" -d '{"quantity": 1}'
curl -X POST http://127.0.0.1:8000/products/coffee/reservations -H "content-type: application/json" -d '{"quantity": 0}'
```

The last three calls show the three error classes handled by the shared error
engine:

| Situation | Result |
| --- | --- |
| Unknown product | Feature-defined `404 product_not_found` problem |
| Insufficient stock | Feature-defined `409 product_unavailable` problem, including typed extension members |
| Invalid request body | Built-in normalized `422 request_validation_error` problem |

`catalog/errors.py` deliberately has no application-wide problem-type URL. Its
`ErrorRegistry` is local to the feature. `Composition` merges that registry
and `ErrorOptions(type_base=...)` resolves every feature error type to the
same public namespace. The catalog's `APIRouter` declares contracts
with `raises=[...]`, so `/openapi.json` documents the `404` and `409` responses
without duplicating schemas. The lower-level
`ErrorRegistry.responses()` API remains supported for ordinary FastAPI routers
and incremental migrations.

The router only validates HTTP input and calls `CatalogService`; it receives
that service through Dishka. `catalog/__init__.py` contributes the provider,
router, and error registry as one `Feature`, so removing the feature removes
all of those pieces together.

To turn a feature off, remove `catalog_feature` from `Composition` in `app.py`.
Its routes and error definitions disappear together.

The routes declare stable `operation_id` values. The
[generator configuration](fastapi-canon.toml) maps those IDs to `catalog.get`,
`catalog.reserve` and `status.health` in the generated client. From the root:

```console
uv run --frozen fastapi-canon generate --config examples/showcase/fastapi-canon.toml
uv run --frozen fastapi-canon generate --config examples/showcase/fastapi-canon.toml --check --diff
uv run --frozen python examples/showcase/consume.py --base-url http://127.0.0.1:8000
```

The saved [contract](contracts/openapi.json) is committed. The SDK and its
ownership manifest live in ignored `dist/generated/python/catalog_client`.
The [consumer](consume.py) exposes that package on its import path and closes its
HTTP resources with `async with`. Rebuild `dist/` after declaration changes;
generated files are disposable and should not be edited manually. Source/API
checks and the full command sequence are in the getting-started guide.

The documented source-check command covers the catalog slice's two operations.
Scanning the entire showcase root currently retains CAN003 for facade re-exports;
composition inspection covers the mounted application's three operations.
The integration suite checks source/preflight commands, exact saved/live/offline
contract drift and the real generated-client HTTP success/problem path.
