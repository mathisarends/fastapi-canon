# Testing

- Unit-test services with a small fake port; test routes, DI and transactions
  against the real app (`with TestClient(create_app()) as client:`), no mocks.
- Assert status, body, `application/problem+json` with `code`, and OpenAPI IDs.
- Replace bindings with `Composition(..., overrides=[TestProvider])`.

```console
uv run fastapi-canon check composition --app backend.app:create_app
uv run fastapi-canon check errors src --strict --require-complete
uv run fastapi-canon explain <code>
```

`check errors` takes the source root containing your package and reports
undeclared (`CAN001`), unmapped (`CAN002`) and unresolved (`CAN003`) errors.
Follow the trace before adding to `raises=`; declare errors clients should handle.
The `--app` form checks installed routes and contracts. To preflight provider
wiring on a fresh app, pass the declaration instead: `check composition backend.app:composition`.

Guide: [error checking](https://github.com/mathisarends/fastapi-canon/blob/main/docs/07-error-checking.md).
