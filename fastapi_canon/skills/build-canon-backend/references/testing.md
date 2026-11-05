# Test a consumer feature

Choose the behavior boundary before the filename. Mirror the source separately
under unit, integration and contract roots, retaining the feature/layer path.
The application's instructions, architecture policy and existing fixtures take
precedence over these illustrative paths.

| Behavior | Boundary | Example path below `backend/tests/` |
| --- | --- | --- |
| Domain rule or service decision | Unit; explicit local fake if needed | `unit/features/projects/application/test_projects.py` |
| SQL mapping and persistence | Integration with a real database | `integration/features/projects/infrastructure/test_repository.py` |
| Composed route, DI, auth, transaction and errors | Integration with the real app graph | `integration/features/projects/presentation/test_router.py` |
| OpenAPI schema, public problem identity or generated SDK | Contract with real exporters/runtime | `contract/features/projects/presentation/test_router.py` |

A service test using SQL is an integration test even if its path contains
`application/`. Use `test_<package>.py` for package behavior and descriptive topic
suffixes when splitting a module's scenarios.

## Observable outcomes

For the [HTTP walkthrough](http-feature.md), run the composed app with
`with TestClient(create_app()) as client:` and verify:

- The exact success status and typed response data.
- Missing data produces 404 and `application/problem+json` with the declared
  `type` and `code`, plus the reviewed public detail.
- Invalid input becomes a Canon validation problem.
- OpenAPI contains the stable operation ID, success model, and matching problem
  response schema/media type. Runtime and schema should describe the same API.

Use real collaborators in integration and contract tests, with no mocks. Unit
tests may replace a port with a small explicit fake; follow the local limit of
one object-targeted patch when a patch is necessary. Do not inspect private
production attributes or encode source/style policies as AST tests.

When testing writes, observe committed data from a fresh request/session after
the producing scope has closed. Verify rollback after an escaping exception and
resource cleanup. SQLite demonstrates SQLite behavior; PostgreSQL locking or
concurrency claims require the real target database.

For streams verify admission errors, business events, terminal problem/completion,
unexpected EOF and cancellation cleanup. For workers and sockets verify fresh
scopes per operation. Use finite local deadlines for async/network/process waits,
and observe readiness/state rather than sleeping. Exercise actual generated
clients when the public contract or generation behavior changes.

## Verification decisions

### Composition and operation IDs

The [HTTP walkthrough](http-feature.md) exposes its declaration as
`backend.app:composition`. Check it before installation:

```console
uv run fastapi-canon check composition backend.app:composition --format json
```

Use the actual module exporting a `Composition` object, not an app factory.
The command imports that module and checks a fresh app without entering lifespan
or resolving resources. `canon.routing.E002` reports duplicate HTTP/SSE operation
IDs, their owning features, both effective method/path locations and a hint to
set distinct `operation_id` values. Text output is also available. Collisions
exit 1; adding `--require-complete` exits 2 if dependent checks were skipped.
`Composition.build(app)` raises for these errors as well.

Check both automatic IDs and explicit overrides. Reusing a handler or mounting a
router under a new URL prefix does not give it a new ID. Rename the function,
choose a distinct router namespace or explicitly name the separate operation;
do not append disambiguating suffixes automatically. `fastapi-canon generate`
also rejects duplicate IDs and SDK naming conflicts before writing output,
including generation from saved contracts.

### Application gates and error declarations

In the consumer, run the existing lint, typing, test and architecture gates. Where
Smelt is configured, inspect the affected context before implementation and run
changed-file plus full policy checks afterwards. Do not transplant Canon's
library layer policy into an application; classify feature wiring explicitly.

For error-declaration analysis, the supported command is:

```console
uv run fastapi-canon check errors src --strict --require-complete
```

Use the actual consumer source root (for example `backend/src`). The checker
covers supported source paths in `APIRouter` HTTP/SSE/WebRTC operations and
`RpcChannel` methods. Dynamic calls and abstract Dishka ports can leave analysis
incomplete. This source analysis is separate from imported composition checks. Inspect
`CAN001` (missing declaration), `CAN002` (missing mapping) and `CAN003` (unresolved
analysis) using [error-checking.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/error-checking.md); do not claim complete coverage by
dropping the completeness flag or adding broad suppressions.

Run the checker in the application's environment: calls into installed
libraries count as raising no application errors only when their packages can
be located. Pass application callbacks to libraries as functions or methods,
not through dynamic lookups, and keep validators that raise application errors
out of Pydantic models so the checker can follow them.

If contracts generate clients, run `fastapi-canon generate --check --diff` after
generation. Report executed checks accurately and distinguish an unexecuted
browser/target-database suite from a passing lint or type check.
