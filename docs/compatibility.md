# Compatibility

## Canon 0.19.0

The published source passed exact-commit CI on Linux/Python 3.12–3.14 and
Windows/Python 3.14, including PostgreSQL and Chromium/WebRTC contracts. Both
20-combination installed-wheel matrices passed on the actual publication
artifacts: current dependencies on Python 3.14 and minimum direct dependencies
on Python 3.12. Installed SSE CLI checks cover core, HTTPX and HTTPX2; the
installed WebRTC client compiles.

All 15 public-index installation combinations passed after the original
publication run's stale index cache was invalidated. PyPI hashes, downloaded
GitHub wheel/sdist files, the attached manifest and release tag agree with the
verified source. See the [publication record](releases.md#current-versions).

## Canon 0.18.0

The published source passed exact-commit CI on Linux/Python 3.12–3.14 and
Windows/Python 3.14, including PostgreSQL and Chromium/WebRTC contracts. Both
20-combination installed-wheel matrices passed: current dependencies on Python
3.14 and minimum direct dependencies on Python 3.12. Installed SSE CLI checks
cover core, HTTPX and HTTPX2; the installed WebRTC client compiles. All 15
public-index installation combinations passed after stale index results were
retried on the original publication run.

A fresh Windows/Python 3.12 public-index installation passed the combined
transport, telemetry, MCP and upload smoke, including application-router RPC
testing and error propagation through lifespan cleanup. The application-router
test helper also passed a separate installed-wheel check with FastAPI 0.121.0
and Pydantic 2.11.0 on Python 3.12. PyPI file hashes, downloaded GitHub assets,
release notes, manifest and tag agree. See the
[publication record](releases.md#current-versions).

## Canon 0.17.0

The published source passed full CI on Linux/Python 3.12–3.14 and Windows/Python
3.14, including PostgreSQL and Chromium/WebRTC contracts. Both 20-combination
wheel matrices passed with current and minimum dependencies. Installed SSE and
WebRTC CLI/client checks and all 15 public-index installation combinations passed.
PyPI hashes, downloaded GitHub assets, release notes, manifest and tag agree.
See the [publication record](releases.md#current-versions).

## Canon 0.16.0

The published source passed exact-commit CI on Linux/Python 3.12–3.14 and
Windows/Python 3.14, including the minimum-dependency Python suite and live
PostgreSQL contracts. Both eighteen-combination installed-wheel matrices passed
from the original upload artifacts: current dependencies on Python 3.14 and
minimum direct dependencies on Python 3.12. Installed SSE CLI checks cover no HTTP
backend, HTTPX and HTTPX2; the installed WebRTC CLI generates a client that compiles.
Real Chromium/WebRTC scenarios passed, and the native telemetry bridge was also
checked locally with FastAPI 0.143.0 and 0.162.0 on Python 3.12.

All thirteen public-index installation combinations passed. A separate fresh
Windows/Python 3.12 installation passed the combined transport, telemetry and MCP
smoke with FastAPI 0.162.1. PyPI hashes, downloaded GitHub assets, release notes,
the tag and the original dry-run/publication manifests agree. These bounded
checks do not establish support for every dependency combination. See the
[publication record](releases.md#current-versions); historical evidence below
remains unchanged.

## Upstream internals

Canon relies on some upstream behavior that is not documented as public API.
The table records each coupling and how it is guarded. Remove a row when the
code moves to a documented API.

| Upstream | Internal behavior | Used by | Guard |
| --- | --- | --- | --- |
| FastAPI | `app._telemetry` configuration dictionary | `otel` native bridge | Field check before installation; telemetry bridge check |
| FastAPI | `APIRoute.dependant` / `Dependant` fields and `ModelField` attributes | Composition inspection, WebRTC contracts | Introspection and contract tests |
| FastAPI | `iter_route_contexts` and `RouteContext` | Effective route paths, contract export and selection | Core minimum 0.143.1, the first version with WebSocket paths for included routers; route and contract tests |
| FastAPI | Mutating `router.routes` before `include_router` | Request-scope installation | Scope tests for direct and included routes |
| FastAPI | `openapi_extra` passed by identity to included routes | Declaration origins and endpoint lookup | Introspection tests through nested includes |
| Dishka | Container registry, provider factories and `__dishka_orig_func__` | Composition checks, injected routes | Range `<1.11` |
| Dishka | `dishka.integrations.base` injection helpers | HTTP, WebSocket and WebRTC injection | Range `<1.11` |
| aiortc | Transceiver `_codecs` / `_headerExtensions` | Re-offers keep negotiated codecs | Range `<1.16` |
| aioice | `_connection._nominated` / `_protocols` | Selected-pair stats, Windows close workaround | Best effort; failures are suppressed |

FastAPI releases minor versions too often to cap the core range per minor.
Instead, the weekly `Upstream canary` workflow upgrades `uv.lock` in CI only and
runs `scripts/check.py all`, the telemetry bridge check and the distribution
smoke against the newest versions the declared ranges allow on Python 3.12–3.14. On 2026-11-05 the
full suite passed with FastAPI 0.162.0, Starlette 1.10.0 and aioice 0.13.0.
Dishka and aiortc ranges are capped at the verified minor and widened in a
release after the canary passes. Run the canary manually with
`workflow_dispatch` before widening a range.

## MCP integration

Canon 0.15.0 adds the optional `mcp` extra with FastMCP >=4.1.3,<5 and
Pydantic >=2.12,<3; core installations retain Pydantic >=2.9. The minimum
installed-wheel matrix exercises FastMCP 4.1.3 and Pydantic 2.12.0 on Python 3.12.
Both wheel matrices and public-index verification exercise a mounted HTTP tool,
injected input schema, tool result and Dishka request-scope cleanup. Core-only
smokes check that importing MCP declarations does not load FastMCP. See the
[MCP guide](integrations/mcp/README.md) for supported handlers and auth ownership.

## WebSocket router boundary

`CanonWebSocketRouter` registers endpoints with `APIRouter.websocket()` and integrates through
`include_router()`. Connection dependencies use `Depends`, and handlers use the
public `WebSocket` constructor, properties and methods. Custom-handler observation
wraps the public send/receive callables in a separate WebSocket instance.
Python signature metadata belongs to Canon's own handler functions.
[FastAPI router API](https://fastapi.tiangolo.com/reference/apirouter/),
[WebSocket API](https://fastapi.tiangolo.com/reference/websockets/).

The installed-wheel WebSocket smoke passes on Python 3.12 with FastAPI 0.121.0
and Starlette 0.49.3, and with FastAPI 0.143.3 and Starlette 1.10.0. It exercises
RPC, typed sockets, connection dependencies and a custom handler with middleware.
The repository's locked suite additionally covers dependency cleanup, denial
responses, disconnects, cancellation and telemetry.

## Database integration and scope ownership

Canon 0.13.0 raises the core minimum to FastAPI 0.121 for function-scoped
HTTP cleanup. Database tests exercise SQLite/aiosqlite and real PostgreSQL/asyncpg,
including transactions, SQLModel sessions and Alembic upgrades preserving data.
The installed-wheel matrix also covers the new optional extras. Historical
published-version evidence below remains unchanged.

## Native telemetry bridge

The native bridge is exercised from built wheels on Python 3.12 against
FastAPI 0.143.0 and the locked 0.143.3 version, with actual SDK span exporters and
metric readers. The check covers the shared private configuration dictionary,
disabled auto-configuration, provider identity, repeated composition, exclusion,
HTTP and port spans/metrics, and borrowed provider ownership. CI runs the same
minimum/locked checks; this does not establish support for every future FastAPI
version. Missing native configuration fields are rejected before installation.

```console
uv run --frozen python scripts/check_telemetry_compatibility.py --python 3.12
```

Use `--fastapi VERSION` repeatedly to check additional releases explicitly.

## Package versions

Only `fastapi-canon` is an active release target. The standalone transport rows
below record historical publications; their versions do not select the current
integration implementations, which share Canon's version.

| Package | Published |
| --- | --- |
| `fastapi-canon` | 0.19.0 |
| `fastapi-streams` | 0.5.0 |
| `fastapi-rpckit` | 0.4.1 |
| `fastapi-webrtc` | 0.3.0 |

**Canon 0.16.0** was published on 2026-11-07. Upgrade the server and regenerated
SDKs together using the [0.16.0 guide](package-migration.md#upgrade-to-0160). The
[publication record](releases.md#current-versions) links the verified artifacts and runs.

**Canon 0.15.0** was published on 2026-11-04. The unified WebSocket stream API
requires coordinated server/client upgrades and regenerated SDKs; the optional
MCP integration selects its own FastMCP/Pydantic requirements. Both
eighteen-combination installed-wheel matrices and all thirteen public-index
installation combinations passed. See the
[0.15.0 upgrade guide](package-migration.md#upgrade-to-0150) and
[verified publication record](releases.md#current-versions).

Canon 0.14.0 was published on 2026-11-03. Core Python/runtime dependency
ranges remain unchanged from 0.13.0. Both seventeen-combination installed-wheel
matrices and all twelve public-index installation combinations passed. Database
startup validation can be delegated explicitly, engine contexts can own
instrumentation, and Alembic downgrades require an explicit revision. The bundled
backend skill installs through the core CLI. See the
[0.14.0 upgrade guide](package-migration.md#upgrade-to-0140) and
[verified publication record](releases.md#current-versions).

Canon 0.13.0 was published on 2026-11-02. The core requires FastAPI >=0.121;
the optional database, SQLite, PostgreSQL, SQLModel and migrations extras add
resource ownership and Alembic tooling. Migrate to keyword-only
`Composition(features=...)` and `CanonWebSocketRouter`, use independent service
scopes for long-lived work, and update WebSocket trace queries. See the
[0.13.0 upgrade guide](package-migration.md#upgrade-to-0130) and
[verified publication record](releases.md#current-versions).

Canon 0.12.0 was published on 2026-11-02. Base Python and runtime dependency
ranges remain unchanged; the new `otel-httpx` and `otel-sqlalchemy` extras add
explicit resource adapters. Pass `instrument(...)` directly to
`Feature.instrumentation` and configure `Telemetry` through composition rather
than custom provider bindings. HTTP exclusions also suppress participating
downstream instrumentation. See the
[0.12.0 migration notes](../CHANGELOG.md#0120---2026-11-02)
and [verified publication record](releases.md#current-versions).

Canon 0.11.0 was published on 2026-11-01 with the same Python and runtime
dependency ranges as 0.10.0. Its telemetry API requires migration from
provider-level `observe()` to `Feature(instrumentation=[instrument(...)])`.
Regenerate Python RPC clients to receive the private dispatch-table fix; their
error exports and wire behavior are unchanged. See the
[0.11.0 migration notes](../CHANGELOG.md#0110---2026-11-01)
and [verified publication record](releases.md#current-versions).

Published versions and assets are linked in the
[verified release records](releases.md#current-versions).
Canon 0.10.0 was published on 2026-11-01. It includes the unpublished 0.9.0
consolidation; follow the [package migration guide](package-migration.md)
and regenerate clients using the 0.10.0 changelog notes.
Streams 0.5.0, Canon 0.8.1 and RPCKit 0.4.1 were published on 2026-10-25; Canon
0.8.0 earlier that day; WebRTC 0.3.0 on 2026-10-24. Follow the
[Streams 0.5.0 notes](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-streams.md#050---2026-10-25)
when regenerating Python stream clients, the
[Canon router-policy migration notes](../CHANGELOG.md#080---2026-10-25)
when updating constructor wrappers and the
[WebRTC migration notes](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-webrtc.md#030---2026-10-24)
before upgrading custom engines. The [release record](releases.md#current-versions)
links the verified source and publication workflows.
Package metadata defines dependency ranges; the root `uv.lock` selects development
versions and does not establish support for every resolvable combination.
Canon 0.10.0 combines the transport code from the unpublished 0.9.0 consolidation
in one wheel. Feature extras select third-party dependencies; no extra requires an
earlier distribution. Base composition retains its lower dependency bounds.

Canon 0.10.0 adds the [canonical error bindings and generated-client
handling](../docs/problems.md). The expanded implementation
passed the current-dependency wheel matrix on Python 3.14 and the minimum matrix
on Python 3.12 on 2026-10-31: eleven independent installations per matrix, with
core Pydantic 2.9 support retained. The final release artifacts passed those
matrices again on 2026-11-01. This does not establish support for every combination
allowed by the dependency ranges.

Canon 0.8.0 adds router policies and optional source-based error-contract checking.
Its policy contracts and installed checker are exercised by the current/minimum
installed-wheel smoke matrices and public-index installation checks.

The earlier standalone releases passed exact-source CI, current/minimum wheel
matrices and installed CLI/client checks before publication. Public installs cover
Canon core, each optional extra and both extras together. A fresh Windows/Python
3.14 installation also verified all four published packages together, including
HTTP/SSE/RPC/WebRTC routing and generated RPC SDK execution and compilation.

## Verification scope

The published 0.15.0 source passed exact-commit CI on Linux/Python 3.12–3.14
and Windows/Python 3.14, including live PostgreSQL, scoped types, generated-client
drift and Chromium/aiortc scenarios. Its original upload wheel passed eighteen
combinations with current dependencies on Python 3.14 and minimum direct
dependencies on Python 3.12, plus installed SSE/WebRTC CLI checks. All thirteen
public-index installation combinations passed, including MCP and combined extras.
PyPI hashes, downloaded GitHub assets, both run manifests, changelog-derived
release notes and the tag source agree. See the
[publication record](releases.md#current-versions).

The published 0.13.0 source passed exact-commit CI on Linux/Python 3.12–3.14
and Windows/Python 3.14, including the live PostgreSQL database and migration
job, scoped types, generated-client drift and Chromium/aiortc scenarios. Its
original upload wheel passed seventeen combinations with current dependencies
on Python 3.14 and minimum direct dependencies on Python 3.12, plus installed
SSE/WebRTC CLI checks. All twelve public-index installation combinations passed,
including SQLite transactions, SQLModel and Alembic upgrades. PyPI file hashes,
downloaded GitHub assets, the manifest, release notes and tag source agree; see
the [publication record](releases.md#current-versions).

The published 0.12.0 source passed exact-commit CI on Linux/Python 3.12–3.14
and Windows/Python 3.14, including lint, scoped type checks, all library tests
and generated-client gates. Chromium/aiortc checks passed on Linux/Python 3.14.
The original release wheel passed all thirteen feature/tool combinations with
current dependencies on Python 3.14 and minimum direct dependencies on Python
3.12, plus installed SSE/WebRTC CLI checks. All eight public-index installation
combinations passed, including HTTPX and SQLAlchemy resource adapters. PyPI
hashes, downloaded GitHub assets, the original manifest, release notes and tag
source are verified; see the [publication record](releases.md#current-versions).

The published 0.11.0 source passed exact-commit CI on Linux/Python 3.12–3.14 and
Windows/Python 3.14, including workspace lint, type, test and generated-client
gates. Chromium/aiortc checks passed on Linux/Python 3.14. The original release
wheel passed all eleven feature/tool combinations with current dependencies on
Python 3.14 and minimum direct dependencies on Python 3.12, plus installed
SSE/WebRTC CLI checks. All six public-index installation combinations passed
before GitHub finalization. PyPI hashes, downloaded GitHub assets, the original
manifest, release notes and tag source are verified; see the
[publication record](releases.md#current-versions).

The published 0.10.0 source passed exact-commit CI on Linux/Python 3.12–3.14 and
Windows/Python 3.14, including the repository lint, type, test and generated-client
gates. Linux/Python 3.14 also passed Chromium/aiortc browser checks. The release
wheel passed all eleven feature/tool combinations with current dependencies on
Python 3.14 and minimum direct dependencies on Python 3.12.

After upload, the publish runner lacked npm compiler dependencies. Recovery
verified the original PyPI hashes and all six public-index installation
combinations on Windows/Python 3.14, plus the combined extras on Windows/Python
3.12, before finalizing the GitHub Release from the original artifacts. See the
[release record](releases.md#current-versions) for source, CI, workflow and asset
links. Per-run manifests and artifact hashes remain in Actions and GitHub Releases.

The unified 0.9.0 source has been checked locally on Windows with Python 3.14
and current compatible dependencies, plus the minimum direct dependency matrix
on Python 3.12. Both matrices install one Canon wheel across eleven feature/tool
combinations without legacy distributions. Installed SSE CLI checks cover no
HTTP backend, HTTPX and HTTPX2; the installed WebRTC CLI generates a client that
compiles. Chromium scenarios cover the aiortc signaling and media paths. This
local validation does not replace exact-source CI or establish a publication.

Canon core requires FastAPI >=0.143.1; the optional `otel` extra adds
OpenTelemetry API >=1.40,<2. Core declarations and disabled Dishka decorators
require no OpenTelemetry imports. Recent FastAPI
versions have their own OpenTelemetry API dependency. The installed-wheel matrix
covers API-only `fastapi-canon[otel]` and the reference trace tests with an application
SDK. Runtime telemetry is exercised with the versions selected in `uv.lock`;
minimum dependencies are checked separately. See the
[telemetry guide](telemetry.md) for native FastAPI
bridging, instrumentation ownership, and operation-span limits.

CI runs the root Python suite and reference-service suite on Linux with Python
3.12–3.14 and Windows with Python 3.14. Linux/Python 3.14 also runs
Chromium/aiortc browser tests and installed
artifact checks. A separate job resolves the lowest direct dependencies the
declared ranges allow (`uv lock --resolution lowest-direct`) and runs the full
Python suite on Python 3.12. Because the development environment installs every
extra together, it exercises their jointly lowest versions, for example FastAPI
0.143.3 rather than the core minimum 0.143.1; the installed-wheel minimum matrix
covers each combination at its own bounds.

Pushes to `main`, manual runs and pull requests labeled `full-ci` run all of these
checks. Architecture, lint, types, client drift and the standalone TypeScript
suites run once on Linux/Python 3.12. Python and generated-client runtime tests
run across the selected Python/platform matrix. Other pull requests select Python
3.12 and 3.14; documentation-only changes skip the suite, and dependency, browser
and artifact checks run when their inputs change. See the
[active CI workflow](../.github/workflows/ci.yml).

The installed-wheel matrix covers:

| Combination | What it establishes |
| --- | --- |
| `[otel]` | Native telemetry and observed ports with the API alone, without an SDK |
| `[otel-httpx]` | Explicit per-client HTTPX instrumentation and cleanup |
| `[otel-sqlalchemy]` | Engine-local sync/async SQLAlchemy instrumentation and pool observations |
| Canon base | Composition with optional tools and engines absent |
| `[uploads]` | Single/repeated file parts, scalar form fields and binary responses using the installed parser |
| `[database]` | Database declarations without a driver or Alembic installed |
| `[database,sqlite,migrations]` | SQLite transactions, Alembic upgrades and schema checks |
| `[database,sqlite,migrations,sqlmodel]` | SQLModel sessions on the same transaction lifecycle |
| `[database,postgres,migrations]` | PostgreSQL driver and migration tooling installation; live database contracts run in CI |
| `[sse]` | SSE runtime with the required FastAPI/Pydantic bounds |
| `[websockets]` | RPC and typed/raw/binary sockets |
| `[webrtc]` | Session declarations, signaling, admission and missing-engine diagnostics |
| `[webrtc,aiortc]` | Optional engine installation |
| `[codegen]` | Offline specialized generation and workflow checks without transport/engine extras; execution of a generated JSON HTTP client |
| `[uploads,codegen]` | Multipart runtime and generator dependencies installed together |
| `[sse,codegen]` | SSE contracts and clients |
| `[websockets,codegen]` | OpenRPC export and client execution/compilation |
| `[sse,websockets,codegen]` | Shared application state and Dishka cleanup |
| `[sse,websockets,webrtc,aiortc,codegen,otel,mcp,uploads]` | Combined routing and client generation |

Run `scripts/check_distributions.py` through the locked root environment for
current compatible dependencies. Add `--python 3.12 --dependencies minimum` for
the lowest jointly satisfiable direct runtime/extra bounds; transitive dependencies
resolve normally. Both modes build and install real artifacts in fresh environments
without local source overrides. Inventories are written to ignored
`dist/compatibility-{latest,minimum}.json`. This is a bounded smoke matrix, not
exhaustive testing of every compatible dependency combination.

The installed Streams CLI is also tested with no HTTP backend, HTTPX alone and
HTTPX2 alone. The installed WebRTC CLI regenerates the saved practice contract and
browser client, verifies provenance/drift and compiles the generated TypeScript.
See [contributing](contributing.md) for commands and [releases](releases.md) for
exact-source CI, dry-run and publication gates. Per-run results and artifact hashes
belong in Actions and GitHub Releases.

## External consumer limits

Cara's exported RPC contract can establish SDK generation/import/compilation and
drift compatibility. It cannot establish installation, mounting, authentication,
Dishka or media behavior of Cara's full application. Follow
[RPC parity and consumer checks](rpc-parity.md) for the installed-wheel command
and remaining migration scope.

For the preceding consumer integration releases, Maximus reported successful
native Linux Chromium/GStreamer integration and browser fallback tests. These
are consumer-reported results, not independent workspace deployment verification.
Deployed TURN/NAT, PostgreSQL concurrency and proxies still require
application-side validation.

Canon 0.17.0 verifies separate SSE/RPC clients and the version-2
generation/check workflow. HTTP client generation belongs to external OpenAPI
tooling. Prior common-SDK checks describe older release evidence and do not
establish the removed facade as a current API. See [generation](codegen.md).
