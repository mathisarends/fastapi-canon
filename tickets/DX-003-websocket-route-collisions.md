# DX-003: Reject duplicate WebSocket paths across features

- Status: Done
- Priority: P1
- Kind: Verified bug
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: New review finding; extends C3.

## Problem and reproduction

Run this with `uv run --frozen python`:

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature, RpcChannel


def make_router(namespace: str) -> APIRouter:
    channel = RpcChannel(namespace=namespace)

    @channel.method("read")
    async def read() -> str:
        return namespace

    router = APIRouter()
    router.include_channel(channel, path="/rpc")
    return router


composition = Composition(
    features=[
        Feature(name="one", routers=[make_router("one")]),
        Feature(name="two", routers=[make_router("two")]),
    ]
)
app = FastAPI()
print(composition.check(app).has_errors)
composition.build(app)
print("build accepted both WebSocket paths")
```

Baseline: `False`, then a successful build. The existing duplicate check only
sees routes included into the same application router. Inspect
[APIRouter inclusion](../fastapi_canon/api_router.py) and
[composition preparation](../fastapi_canon/feature.py).

## Scope

Validate effective WebSocket path uniqueness at composition preflight.
Cover JSON-RPC and native WebSocket routes without changing protocol names,
connection semantics or HTTP routing.

## Acceptance criteria

- [x] Reject duplicates across features and against existing app routes, including equivalent parameter names and nested prefixes.
- [x] Report a stable diagnostic naming both features/endpoints; rejection happens before application mutation.
- [x] Cover a native WebSocket route colliding with an included JSON-RPC channel.
- [x] Allow HTTP and WebSocket endpoints to share a URL because they use different ASGI scope types.
- [x] Distinct WebSocket URLs remain valid; explicit channel resolvers and contexts retain their behavior.
- [x] Document the newly rejected configurations under Breaking changes with path-migration guidance.

## Verification

Extend the existing duplicate-path scenario with separate feature routers. Use real collaborators and bounded ASGI connection tests.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#83](https://github.com/mathisarends/fastapi-canon/pull/83).
- Final commit and verification: source fix `91b0c1decc00f94997d4dc80b1942d46446ba959`,
  based directly on main `d5daf91` (including merged DX-001). Fresh fetch/merge
  before PR creation confirmed origin/main was already included.
  The exact ticket repro now rejects with canon.routing.E004. Focused feature,
  router, introspection and composition suites: 88 passed, including bounded
  real RPC/native WebSocket and HTTP calls, explicit resolver/context scenarios
  and unchanged-app assertions. Full root pytest: 2,438 passed, 26 skipped,
  153 warnings on Windows/Python 3.14; PostgreSQL cases need an external database.
  Mypy, Ruff lint/format, changed Smelt and full pre-commit including full Smelt
  passed. Standalone distribution/browser matrices were not run for this
  routing-preflight change. Merged into main in `6a49911`.
- CI follow-up: reproduced the telemetry compatibility job's missing cached
  route regex with an installed wheel under Python 3.12/FastAPI 0.143.0.
  The HTTP collision check now derives a missing regex from the effective
  declared path, preserving converters. The installed-wheel smoke also checks
  a late collision after telemetry installation. The full smoke passed on
  Python 3.12 with FastAPI 0.143.0 and locked 0.143.3; focused routing,
  introspection and telemetry regressions passed (69 tests).
- Remaining follow-ups: late WebSocket mutation of an already built application
  is outside this preflight; broader installed transport descriptions/checks
  remain a separate DX-029 decision. Mounted apps remain separate boundaries.
