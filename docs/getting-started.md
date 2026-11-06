# From one feature to a generated client

Use the runnable [showcase](../examples/showcase/README.md) for this path:
declare a Catalog feature, inject its service, compose the app, check its errors
and call it through a generated SDK. Run commands from the repository root.
They follow this checkout and its lockfile; the published release remains 0.15.0.
Unreleased changes are prepared for 0.16.0, as recorded in the
[changelog](../CHANGELOG.md#unreleased), rather than already published.

## Install the checkout

```console
uv sync --frozen --all-groups
```

The development environment includes the server, code generator and SDK's HTTPX
backend. Application installations choose the extras they need; see
[installation](package-migration.md) and [tested dependencies](compatibility.md).

## Declare the feature

Follow the catalog's [service](../examples/showcase/features/catalog/service.py),
[provider](../examples/showcase/features/catalog/providers.py),
[errors](../examples/showcase/features/catalog/errors.py) and
[router](../examples/showcase/features/catalog/router.py).
The HTTP layer receives an application service through `Inject`:

```python
@router.get("/{product_id}", operation_id="get_product", raises=[product_not_found])
async def get_product(product_id: str, catalog: Inject[CatalogService]) -> Product:
    return catalog.get_product(product_id)
```

This is an excerpt of the runnable router, which imports `APIRouter` and `Inject`
from `fastapi_canon`. The domain exception stays ordinary Python. Its `Error`
defines the public status/code, and `raises` declares that operation's contract.
The feature's Dishka provider supplies the service; no injection decorator is
needed. See [routers](routers.md) and [shared problems](problems.md) for options.

The [feature export](../examples/showcase/features/catalog/__init__.py) bundles
the router and provider in one `Feature`. The
[composition root](../examples/showcase/app.py) passes it to `Composition` and
builds a FastAPI app. `ErrorOptions(type_base=...)` supplies the public problem
namespace for all features. See [composition](composition.md) for the full API.

## Check before serving

```console
uv run --frozen fastapi-canon check errors examples/showcase/features/catalog --strict --require-complete
uv run --frozen fastapi-canon check composition examples.showcase.app:composition --require-complete --fail-on warning
uv run --frozen fastapi-canon inspect examples.showcase.app:app --format json
```

The source command covers the catalog's two declarations without importing them.
Composition preflight checks dependencies and contracts; inspection describes all
three installed operations, including health. Those commands import the app but
do not enter lifespan. Source completeness concerns supported declarations;
runtime integration tests establish actual request behavior. See
[source checks](error-checking.md) and [composition checks](system-checks.md).

## Generate the SDK

The [configuration](../examples/showcase/fastapi-canon.toml) exports the composed
app and maps `get_product` to `catalog.get`, preserving its HTTP operation ID.

```console
uv run --frozen fastapi-canon generate --config examples/showcase/fastapi-canon.toml
uv run --frozen fastapi-canon generate --config examples/showcase/fastapi-canon.toml --check --diff
```

The saved [OpenAPI contract](../examples/showcase/contracts/openapi.json) is
committed; the Python package is generated under the example's ignored `dist/`.
Edit server declarations/configuration and regenerate. The check command compares
exact output without rewriting it. See the [generation guide](unified-canon-concept.md)
for ownership, offline sources, names and TypeScript targets.

## Serve and call it

Start the server in one terminal:

```console
uv run --frozen uvicorn examples.showcase.app:app --host 127.0.0.1 --port 8000
```

Run the complete [generated-client consumer](../examples/showcase/consume.py) in
another terminal:

```console
uv run --frozen python examples/showcase/consume.py --base-url http://127.0.0.1:8000
```

It calls `await client.catalog.get(product_id="coffee")`, then handles the
`ProblemError` for a missing product. On a fresh server it prints:

```json
{"id": "coffee", "available": 3}
{"code": "product_not_found", "status": 404}
```

The SDK owns its HTTP client and closes it with `async with`. The catalog service
uses APP scope, so its in-memory stock belongs to the running application.
Composition owns the container and cleanup; HTTP dependency scopes close after
the handler and response validation. See [scope ownership](database.md).
Stop the foreground server after the walkthrough.

For streams, distinguish an HTTP failure before response start from a terminal
protocol problem after streaming begins; see [stream contracts](streams.md).
Continue with the [combined HTTP/SSE/RPC SDK example](../examples/canonical_errors/README.md)
for real calls across transports and their resource cleanup.
