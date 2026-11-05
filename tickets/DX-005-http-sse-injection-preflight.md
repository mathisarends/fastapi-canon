# DX-005: Validate HTTP and SSE injected dependency roots before serving

- Status: Done
- Priority: P1
- Kind: Coverage improvement
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: Additional scope of review finding about missing DI roots; D1/C3.

## Problem and reproduction

Inspect [container preparation](../fastapi_canon/feature.py) and
[endpoint injection](../fastapi_canon/injected_route.py). Composition validates
the provider graph but does not explicitly validate every injected endpoint root.

To establish the current behavior, declare `async def read(store: Inject[Store])`
on an HTTP router with no Store provider, then compare check(), build() and an
actual request. Repeat for a finite SSE generator and for an unrelated valid
provider graph. The demonstrated missing-RPC-binding case is tracked separately
in [DX-004](DX-004-rpc-injection-preflight.md); do not assume identical HTTP behavior
without running these cases.

## Scope

### Verified baseline (parent DX-004 plus the DX-003 CI fix)

Real bounded ASGI requests confirmed these cases before implementation:

| Endpoint | Binding | check/build | Actual request |
| --- | --- | --- | --- |
| HTTP async | None | Accepted | RuntimeError: no request container |
| HTTP async | Unrelated valid graph | Accepted | NoFactoryError |
| HTTP async | APP or REQUEST Store | Accepted | 200; provider opened/closed |
| Finite async SSE | None or unrelated graph | Accepted | 200 with terminal canon.problem |
| Finite async SSE | APP Store | Accepted | Event and canon.complete; provider cleanup |
| Finite async SSE | REQUEST Store | Accepted | Terminal canon.problem; Store body never constructed |

HTTP REQUEST scopes already close before streaming. This ticket validates the
available phase; it does not retain REQUEST resources across a stream. Sync
stream arguments cannot extend a resource's lifetime either. Supporting a new
stream-owned resource lifetime would require a separate contract.

Add preflight coverage for HTTP/SSE endpoint Inject and FromDishka roots only.
Keep FastAPI Depends/Security functions, custom route classes and separately
configured containers explicit; do not resolve services to discover graph edges.

## Acceptance criteria

- [x] Record the baseline cases before implementing; turn confirmed missing endpoint bindings into stable preflight findings with route, feature and type.
- [x] Valid Inject and FromDishka endpoints remain absent from public request schemas.
- [x] Cover HTTP async/sync handlers and finite SSE generators with configured APP/REQUEST providers.
- [x] Provider bodies and lifespans are not executed by check(); unrelated FastAPI dependencies are not treated as Dishka services.
- [x] Define behavior for explicit custom route classes or externally installed containers without breaking their ownership silently.
- [x] Document coverage limits and classify stricter configuration validation under the release policy.

## Verification

Use real containers and bounded HTTP/SSE requests in the injected-route and feature-check suites. Coordinate diagnostics with DX-004 without requiring one shared abstraction.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#85](https://github.com/mathisarends/fastapi-canon/pull/85).
  Started on DX-004; targets main after that parent merged.
- Final commit and verification: source `bc13482`, synchronized with main
  (including merged DX-004) in `0d88d51`; the merge changed no source files.
  The recorded baseline now rejects missing/phase-unavailable bindings with
  canon.providers.E003 before installation; valid HTTP APP/REQUEST and SSE APP
  requests still return successful responses and close providers.
  Focused injection/RPC/routing tests: 95 passed; final injection subset: 26 passed.
  Full root pytest: 2,477 passed, 26 skipped, 153 warnings on Windows/Python 3.14;
  PostgreSQL cases require an external database. All 18 installed-wheel minimum
  dependency combinations passed on Python 3.12, including core imports/help and
  generated consumers; the inventory remains in ignored dist/.
  Mypy, Ruff, changed Smelt and full pre-commit passed. Standalone real-browser
  checks were not run. Merged into main in `1819cf7`.
- Remaining follow-ups: FastAPI dependency-function roots, custom route classes
  and external containers retain their documented ownership/coverage limits.
  A new stream-owned REQUEST lifetime is outside this validation ticket.
