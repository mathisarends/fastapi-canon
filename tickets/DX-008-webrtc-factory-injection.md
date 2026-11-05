# DX-008: Make WebRTC factory injection consistent with the shared router

- Status: In progress
- Priority: P2
- Kind: Verified integration gap
- Baseline: `a366702` (2026-11-05)
- Depends on: [DX-005](DX-005-http-sse-injection-preflight.md)
- Earlier review: New review finding; remaining D1 coverage.

## Problem and reproduction

Run this with `uv run --frozen python`:

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Inject
from fastapi_canon.integrations.webrtc import Session


class Store:
    pass


router = APIRouter()


@router.webrtc("/media")
async def media(store: Inject[Store]) -> Session:
    return Session()


app = FastAPI()
app.include_router(router)
```

Baseline: registration fails with FastAPI's invalid-Pydantic-field error before
the factory runs. The factory is currently registered through
[Depends(factory)](../fastapi_canon/integrations/webrtc/router.py). No media engine
is needed to reproduce this registration failure.

Reproduced on the DX-007 parent with Python 3.14: FastAPIError, `Invalid args
for response field`, during decorator registration. The exact reproduction
registers successfully after the WebSocket-specific adapter is applied.

## Scope

Support the common marker on WebRTC factory dependencies while keeping admission
and accepted-session lifetimes explicit. Do not put HTTP-only Request injection
into a WebSocket handshake or load a media engine from the root facade.

## Acceptance criteria

- [x] Inject and FromDishka resolve through Composition for supported WebRTC factories without user @inject boilerplate.
- [x] Required path/header parameters and injected services stay correctly separated in the signaling contract.
- [x] Admission errors retain their HTTP/pre-acceptance behavior; dependency cleanup occurs at the documented phase.
- [x] Use real bounded session scenarios with an existing test engine/collaborator; do not require aiortc for core imports or CLI help.
- [x] Cover missing bindings through the appropriate preflight checks after DX-005, or explicitly report any remaining validation limit.
- [x] Document behavior for coroutine and generator factories and any compatibility impact.

## Verification

Read the WebRTC architecture/tests before choosing the adapter seam. Run WebRTC contract/lifecycle tests and core optional-import smoke checks.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#88](https://github.com/mathisarends/fastapi-canon/pull/88),
  stacked on DX-007 [#87](https://github.com/mathisarends/fastapi-canon/pull/87).
- Final commit and verification: implementation `ce30c36`, scope/migration docs
  `825b3d0`; refreshed and merged `origin/main` before PR creation (already
  current). Focused WebRTC lifecycle/contract/router/CLI suites: 259 passed.
  The eight new real-aiortc signaling/scope cases passed on Python 3.12 and
  3.14. Full root pytest: 2,520 passed, 26 skipped, 153 warnings, one existing
  WebSocket TypeScript compilation timeout; that exact failed test passed on
  immediate isolated retry in 3.35 seconds without a code change. Ruff, mypy,
  changed/full Smelt, all-files pre-commit and the full scoped type gate passed,
  including installed-wheel type completeness (94.8%). Fresh installed WebRTC
  CLI, contract/client drift and generated TypeScript compilation passed.
  Optional-import/CLI-help smoke tests passed. Real browser and full minimum
  matrices were not repeated for this factory adapter.
- Remaining follow-ups: arbitrary nested FastAPI dependencies, converted
  synchronous Dishka Depends wrappers and externally owned containers retain
  their documented validation limits. Per-message REQUEST scopes are explicit;
  no session lifetime or callback injection redesign is included. Status remains
  In progress until merge.
