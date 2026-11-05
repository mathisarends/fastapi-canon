# DX-008: Make WebRTC factory injection consistent with the shared router

- Status: Open
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

## Scope

Support the common marker on WebRTC factory dependencies while keeping admission
and accepted-session lifetimes explicit. Do not put HTTP-only Request injection
into a WebSocket handshake or load a media engine from the root facade.

## Acceptance criteria

- [ ] Inject and FromDishka resolve through Composition for supported WebRTC factories without user @inject boilerplate.
- [ ] Required path/header parameters and injected services stay correctly separated in the signaling contract.
- [ ] Admission errors retain their HTTP/pre-acceptance behavior; dependency cleanup occurs at the documented phase.
- [ ] Use real bounded session scenarios with an existing test engine/collaborator; do not require aiortc for core imports or CLI help.
- [ ] Cover missing bindings through the appropriate preflight checks after DX-005, or explicitly report any remaining validation limit.
- [ ] Document behavior for coroutine and generator factories and any compatibility impact.

## Verification

Read the WebRTC architecture/tests before choosing the adapter seam. Run WebRTC contract/lifecycle tests and core optional-import smoke checks.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
