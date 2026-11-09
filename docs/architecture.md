# Repository architecture

Canon is one Python distribution, built from the root `pyproject.toml`.
All integrations share its version. The reference service is an installable
example, not a release target.

```text
fastapi_canon/          Library and public facades
  error/               Error values and HTTP/OpenAPI adapters
  integrations/        Optional transports, database, MCP and telemetry
  codegen/             Contract export and generation workflow
  checks/              Static error-contract analysis
tests/                 Unit, integration, contract, TypeScript and browser suites
  fixtures/            Shared support, snapshots and generated clients
examples/              Runnable applications
docs/                  Task guides and contributor reference
scripts/               Development, artifact and release commands
```

The root owns the Python and Node manifests, lockfiles, CI and release metadata.
The dependency map and source policies are enforced by [Smelt](../smelt.yml).

## Dependency direction

| Layer | Responsibility |
| --- | --- |
| Values and responses | Framework-independent data, metadata and rendering; standard library and Pydantic only |
| HTTP/OpenAPI adapters | Consume values and the read-only `ErrorCatalog` protocol |
| Routing | Builds on HTTP adapters |
| Composition | Owns feature installation, routing, Dishka and lifecycle integration |
| Integrations | Own their transport or resource lifecycle; transports do not import siblings |
| Generation | Reads contracts and coordinates specialized or external generators |
| Analysis | Checks source without importing application code; standard library only |

The root facade exports application-building names. Optional types such as
`Database` and `RpcChannel` come from integration facades. Core imports and CLI
help must work without generator dependencies, telemetry runtimes or media
engines. Composition delegates optional behavior lazily.

See [public imports](canon.md) for supported consumer namespaces and
[composition](composition.md) for application ownership.

## Tests

Python tests mirror library paths under `tests/unit`, `tests/integration` and
`tests/contract`. Topic suffixes are supported; protocol scenarios are explicit
exceptions in Smelt. Shared snapshots belong in `tests/fixtures`.

All library Python tests run in one root pytest invocation with qualified
imports. The reference service also has an installed-application test run.
Integration and contract tests use real collaborators. Generated clients are
updated through generators and checked for exact drift; distribution checks
install real wheels in fresh environments.

Use [contributing](contributing.md) for commands and
[guardrails](architecture-guardrails.md) for policy changes.
