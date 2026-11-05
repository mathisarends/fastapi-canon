# Repository architecture

Canon is one Python library, built from the root `pyproject.toml`. Its import
package is `fastapi_canon/`; there is no Python or pnpm package workspace.
Optional transports and telemetry ship inside the same distribution and version.

```text
fastapi_canon/                  Library and public facades
  error/                       Error values, catalog and HTTP/OpenAPI adapters
  integrations/                SSE, WebSockets, WebRTC, MCP and telemetry
    database/                  Optional SQLAlchemy ownership and Alembic runner
  codegen/                     Shared SDK planning and generation
  checks/                      Static error-contract analysis
tests/
  unit/                        Isolated behavior, including explicit test seams
  integration/                 Real framework and transport interactions
  contract/                    HTTP schemas, protocol contracts and artifacts
  fixtures/                    Shared test support, snapshots and generated SDKs
  typescript/                  Browser SDK runtime and type checks
  browser/                     Real Chromium/WebRTC sessions
examples/                      Runnable applications and their generated clients
docs/                          Library, integration and contributor documentation
  integrations/                Detailed transport guides
  history/                     Predecessor changelogs and licenses
scripts/                       Development, distribution and release entrypoints
```

The root owns build metadata, dependency groups, `uv.lock`, the Node tooling
manifest and `pnpm-lock.yaml`. The reference service under
`examples/reference-service/` is an editable development dependency and an
independently installable example, not another Canon release target.
`release.toml` selects the repository root as the sole Canon release directory.

## Dependencies

Error values, metadata and rendering depend only on the standard library and
Pydantic. Successful-response metadata builds on those values. HTTP and OpenAPI
adapters consume the read-only `ErrorCatalog` protocol; routing builds on adapters,
and composition owns routing, Dishka and lifecycle integration. Each transport
has its own role and must not import sibling transports or eagerly load optional
engines. Generators may read protocol contracts. Repository scripts sit outside
the runtime package and may orchestrate its supported integrations.

`api_router.py` belongs to composition. Its public `APIRouter` builds on the core
HTTP router and lazily delegates SSE, RPC and WebRTC declarations to their existing
adapters. Composition explicitly permits the SSE dependency alongside RPC and
WebRTC. Core routing does not depend back on transports. Included RPC channels
are independent snapshots, preserving their functions, errors and resolver scopes.
The application layer retains the corresponding transitive SSE edge when
consuming the root facade, alongside its existing RPC and WebRTC edges.
`public_exports.py` owns lazy root lookups for optional integrations. Its outer
exports role may reach the integrations it exposes. Applications, showcases and
development/generation entrypoints may consume that role; lower runtime layers
cannot. Database implementation modules import their lower-level definitions
directly instead of depending back on the root's optional export lookup. The root
remains a re-export-only facade, and core imports do not load RPC, database or
media runtimes.

`contracts.py` belongs to composition and coordinates the existing RPC and WebRTC
contract renderers with native HTTP/SSE OpenAPI. This adds an explicit composition
dependency on WebRTC and its AnyIO lifecycle types; consumers of the root facade
inherit that declared direction. No transport imports another transport.
`contract_routes.py` is an adapter shared by RPC, WebRTC and HTTP introspection
for effective paths.
OpenAPI copying/promotion lives in the core adapter; optional JSON Schema validation
stays in generation, keeping root imports free of generator dependencies.

Core `scopes.py` belongs to composition and owns HTTP function scopes plus
independent `ServiceScope` factories. It has no SQLAlchemy or Alembic dependency.
The optional database integration sits above composition: it contributes an
ordinary Feature and its Dishka provider, and owns database-specific session and
migration behavior. The CLI may consume this integration; core composition does
not depend on it. Root database exports are lazy so core imports and command help
remain available without database runtimes.

`request_logging` belongs to composition. It wraps the lazily built middleware
stack outside the server-error handler, so it can observe the actual 500 response
and successful streaming sends. HTTP problem handlers publish their code and
status in the request scope; the logger never parses response bodies. Optional
trace-context lookup does not load a telemetry SDK or configure process-wide logs.

Telemetry declarations remain importable without optional runtimes. Resource
adapters live in explicit `integrations.opentelemetry.httpx` and `.sqlalchemy`
modules so composition does not transitively depend on resource libraries through
the declaration facade. The telemetry layer explicitly allows SQLAlchemy for its
opt-in engine event adapter; core composition stays independent.

The executable dependency map and source inventory live in `smelt.yml`. Read
[architecture guardrails](architecture-guardrails.md) for the individual rules,
exceptions and test-quality requirements, and [public namespaces](canon.md) for
consumer imports.

## Tests and checks

`diagnostics.py` contains framework-independent immutable descriptions and check
results. `introspection.py` adapts effective HTTP route metadata; composition owns
preflight orchestration, feature checks and the installed snapshot. CLI adapters
consume those same APIs. Canon route declarations retain provenance as Python
metadata that survives FastAPI route copies without entering exported OpenAPI.
See [composition checks](system-checks.md) for scope and developer APIs.

Unit, integration and contract roots mirror library paths independently. For
example, `fastapi_canon/error/openapi.py` maps to
`tests/contract/error/test_openapi.py`; focused topics can use a descriptive suffix.
Existing protocol scenario suites remain explicitly listed in Smelt rather than
silently disabling mirroring for all tests. Integration and contract tests use
real collaborators and prohibit mocks. Shared snapshots live under `tests/fixtures/`.

All Python library tests run from the root in one pytest process using importlib
mode and qualified fixture/example imports. The reference service also has its
own installed-application test run. Feature-specific type/lint policies live in the root configuration and integration
directories; this does not create separate package metadata or environments.
Generated files are updated through their generators and verified for drift.

Run `uv run --frozen smelt context <module-or-path>` before implementation, then
the checks in [contributing](contributing.md). Full Smelt coverage, errors and
warnings gate both CI and the root pre-commit hook. Wheel and sdist checks install
actual artifacts in fresh environments, without source-tree fallback.

MCP declarations live in `integrations.mcp`. Composition lazily installs a fresh
MCP root per application; call middleware borrows the APP container and owns
REQUEST scopes. The registration adapter translates `Inject[T]` before FastMCP
schema creation. No sibling transport or concrete error registry is imported.
Smelt includes the new MCP edges and optional FastMCP dependency transitively
for layers which already consume composition. Runtime imports remain lazy.
