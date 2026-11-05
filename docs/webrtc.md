# WebRTC admission errors

Install `fastapi-canon[webrtc]` and declare sessions with `@router.webrtc(...)` on
the feature's `APIRouter`. Admission failures use the same `Error` definitions
and registry as the router's HTTP operations.

```python
from fastapi import FastAPI
from fastapi_canon import (
    APIRouter,
    Composition,
    Error,
    ErrorOptions,
    ErrorRegistry,
    Feature,
    export_contracts,
)
from fastapi_canon.integrations.webrtc import Session
from fastapi_canon.integrations.webrtc.engines.aiortc import AiortcEngine


class BrowserMissing(Exception):
    pass


BROWSER_MISSING = Error(
    BrowserMissing, status=404, code="browser_missing", title="Browser missing"
)
errors = ErrorRegistry(errors=[BROWSER_MISSING])
router = APIRouter(error_registry=errors)


@router.webrtc(
    "/browser/{browser_id}/video", engine=AiortcEngine(), raises=[BROWSER_MISSING]
)
async def video(browser_id: str) -> Session:
    # Authenticate and load the stream through application services here.
    raise BrowserMissing()


app = Composition(
    features=(Feature(name="browser", routers=[router]),),
    errors=ErrorOptions(type_base="https://example.test/problems"),
).apply(FastAPI())
contract = export_contracts(app, kinds=("webrtc",)).webrtc
```

The engine extra remains independent: install `fastapi-canon[webrtc,aiortc]` for this
example, or provide your own engine. Canon core imports neither WebRTC nor aiortc.

Router-level `raises` and session-level `raises` combine and deduplicate by the
identity of the existing `Error` objects. Definitions outside the router's registry
fail during configuration. `Feature` discovers the router's registry
automatically.

Declare failures reachable from authentication and other FastAPI dependencies
explicitly; the library does not infer which exceptions arbitrary code can raise.
`raises` documents failures and does not restrict runtime exception mapping.

Export with `export_contracts(app)` after composition so errors inherit the
composed registry's type URI base. Each session's `admission_errors` contains status, code, type, title,
description, media type and a self-contained Problem Details JSON Schema. Generated
session descriptors retain this as `admissionErrors`. It describes HTTP rejection
before WebSocket acceptance, independently of HTTP OpenAPI operations.

The composed registry maps application exceptions from dependencies or the session
factory before acceptance. No engine peer is allocated for these denials. After
acceptance, failures use WebRTC protocol errors and teardown; they cannot send an
HTTP problem response. Browser WebSocket APIs do not expose failed handshake
response bodies. Contract metadata therefore does not create typed browser access
to those responses. A separate HTTP admission flow is described in the
[WebRTC integration guide](integrations/webrtc/canon-dishka.md).
