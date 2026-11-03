# Test a consumer feature

Choose the behavior boundary before the filename. Mirror the source separately
under unit, integration and contract roots, retaining the feature/layer path.
The repository's `AGENTS.md`, Smelt policy and existing fixtures take precedence
over illustrative consumer paths.

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

In the consumer, run the existing lint, typing, test and architecture gates. Where
Smelt is configured, inspect the affected context before implementation and run
changed-file plus full policy checks afterwards. Do not transplant Canon's
library layer policy into an application; classify feature wiring explicitly.

For error-declaration analysis, the supported command is:

```console
uv run fastapi-canon check errors src --strict --require-complete
```

Use the actual consumer source root (for example `backend/src`). The checker
covers supported direct `CanonRouter` HTTP operations, not every transport or
dynamic call graph. Abstract Dishka ports can leave analysis incomplete. Inspect
`CAN001` (missing declaration), `CAN002` (missing mapping) and `CAN003` (unresolved
analysis) using `docs/error-checking.md`; do not claim complete coverage by
dropping the completeness flag or adding broad suppressions.

If contracts generate clients, run `fastapi-canon generate --check --diff` after
generation. In the Canon repository itself, run the commands required by its root
`AGENTS.md`, including the full Smelt gate and pre-commit. Report executed checks
accurately and distinguish an unexecuted browser/target-database suite from a
passing lint or type check.
