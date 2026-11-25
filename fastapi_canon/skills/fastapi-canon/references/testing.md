# Testing and verification

The project's own test layout and fixtures take precedence.

| Behavior | Boundary | Path below `tests/` |
| --- | --- | --- |
| Domain rule, service decision | Unit, small explicit fake | `unit/features/projects/application/` |
| SQL mapping, persistence | Integration, real database | `integration/features/projects/infrastructure/` |
| Route, DI, auth, transaction, errors | Integration, real app | `integration/features/projects/presentation/` |
| OpenAPI, problem identity, SDK | Contract, real exporters | `contract/features/projects/presentation/` |

A service test that touches SQL is an integration test. No mocks in integration
or contract tests.

## What to assert

Run the real app with `with TestClient(create_app()) as client:` and check:

- Success status and typed body.
- Declared failures: status, `application/problem+json`, `type` and `code`.
- Invalid input becomes a validation problem.
- OpenAPI has the stable operation ID and matching problem responses.
- Writes are visible from a fresh request; escaping errors roll back.
- Streams: admission errors, events, terminal problem/completion, cancellation.

Use finite deadlines and wait on observable state, not `sleep`. Swap providers
with `Composition(..., overrides=[...])`.

## Checks

```console
uv run fastapi-canon check composition backend.app:composition --format json
uv run fastapi-canon check errors src --strict --require-complete
uv run fastapi-canon generate --check --diff
```

- `check composition` takes the module attribute holding the `Composition`,
  builds a fresh app without lifespan and reports e.g. duplicate operation IDs.
- `check errors` scans source for raised exceptions without a `raises=`
  declaration (`CAN001`), without a mapping (`CAN002`) or not analyzable
  (`CAN003`). Pass the source root that contains your package (e.g. `src`), so
  wiring modules are scanned too, and run it in the app's environment.
  Keep `--require-complete`; don't suppress findings broadly.
- Use `uv run fastapi-canon explain <code>` for any finding.

Details: [error checking](https://github.com/mathisarends/fastapi-canon/blob/main/docs/07-error-checking.md),
[diagnostics](https://github.com/mathisarends/fastapi-canon/blob/main/docs/23-checks-and-diagnostics.md).
