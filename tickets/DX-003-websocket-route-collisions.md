# DX-003: Reject duplicate WebSocket paths across features

- Status: Open
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

- [ ] Reject duplicates across features and against existing app routes, including equivalent parameter names and nested prefixes.
- [ ] Report a stable diagnostic naming both features/endpoints; rejection happens before application mutation.
- [ ] Cover a native WebSocket route colliding with an included JSON-RPC channel.
- [ ] Allow HTTP and WebSocket endpoints to share a URL because they use different ASGI scope types.
- [ ] Distinct WebSocket URLs remain valid; explicit channel resolvers and contexts retain their behavior.
- [ ] Document the newly rejected configurations under Breaking changes with path-migration guidance.

## Verification

Extend the existing duplicate-path scenario with separate feature routers. Use real collaborators and bounded ASGI connection tests.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
