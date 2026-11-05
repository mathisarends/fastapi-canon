# DX-004: Validate RPC handler dependency roots during composition preflight

- Status: Done
- Priority: P1
- Kind: Verified coverage gap
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: New review finding; A1 is fixed, but this is a different missing-binding case.

## Problem and reproduction

Run this with `uv run --frozen python`:

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature, Inject, RpcChannel


class Store:
    pass


channel = RpcChannel()


@channel.method("read")
async def read(store: Inject[Store]) -> str:
    return "ok"


router = APIRouter()
router.include_channel(channel, path="/rpc")
composition = Composition(features=[Feature(name="stores", routers=[router])])
app = FastAPI()
print(composition.check(app).has_errors)
composition.build(app)
print("build accepted an unresolved Store dependency")
```

Baseline: `False`, then a successful build even though Store cannot be resolved.
The same gap must be checked when unrelated, valid providers do exist.

## Scope

Connect RPC Inject requests to the provider graph owned by Composition.
Start with JSON-RPC methods; make unsupported roots visible rather than claiming
universal transport validation. Preserve custom resolver ownership.

## Acceptance criteria

- [x] An unresolved application-service Inject in a mounted RPC method produces a preflight diagnostic with the service type, endpoint/method and feature.
- [x] Exercise both no-container and missing-binding-in-an-existing-container cases.
- [x] Framework-provided connection values and typed hook/context results are not mistaken for missing application providers.
- [x] Explicit custom resolvers remain supported; do not reject their dependencies by inspecting only the Dishka graph.
- [x] Checking never resolves providers, enters lifespans or starts resources; APP/SESSION/REQUEST scope rules remain respected.
- [x] Add migration notes for configurations now rejected; document the precise supported preflight scope.

## Verification

Extend real composed-channel tests and feature-check tests. Assert no provider construction during check(), plus successful requests and cleanup for valid bindings.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#84](https://github.com/mathisarends/fastapi-canon/pull/84),
  stacked on [#83](https://github.com/mathisarends/fastapi-canon/pull/83).
- Final commit and verification: implementation `2729ebf`; verified parent-sync
  source `151635a99af2eb224635e4da16a86864a226dfe9` includes the parent CI fix.
  Exact missing-Store repro rejects with canon.providers.E002 before installation.
  Full root pytest after synchronization: 2,457 passed, 26 skipped, 153 warnings
  on Windows/Python 3.14; PostgreSQL cases require an external database.
  Focused composed-RPC/routing/context/introspection suites: 100 passed.
  Full types gate passed, including installed-wheel public RPC completeness
  (94.8%, minimum 92%), reference application and WebRTC checks.
  Python 3.12 installed-wheel telemetry matrix passed for FastAPI 0.143.0 and
  locked 0.143.3. Mypy, Ruff, changed Smelt and full pre-commit passed.
  Standalone browser/minimum-distribution matrices were not run.
  Merged into main in `f1cc05f`.
- Remaining follow-ups: custom/external resolution and non-method transport
  roots remain explicitly skipped; admission-service roots are outside this
  slice. HTTP/SSE roots remain DX-005 and broader inspection remains DX-029.
