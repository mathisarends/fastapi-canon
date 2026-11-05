# DX-007: Support injected callable objects and partial endpoints

- Status: Open
- Priority: P2
- Kind: Verified bug
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: New review finding after PR #77.

## Problem and reproduction

Run this with `uv run --frozen python` on Python 3.14:

```python
import functools
from fastapi import FastAPI
from fastapi_canon import APIRouter, Inject


class Store:
    pass


class Read:
    async def __call__(self, store: Inject[Store]) -> str:
        return "ok"


async def read(prefix: str, store: Inject[Store]) -> str:
    return prefix


for label, endpoint in [
    ("object", Read()),
    ("partial", functools.partial(read, "ok")),
]:
    try:
        router = APIRouter()
        router.get("/read")(endpoint)
        app = FastAPI()
        app.include_router(router)
        app.openapi()
        print(label, "registered")
    except Exception as error:
        print(label, type(error).__name__, str(error))
```

Baseline: both cases fail with FastAPI's invalid-Pydantic-field error rather
than printing registered. An ordinary injected function registers successfully.
Record the exact failure on Python 3.12 too; annotation introspection differs
between supported interpreters.

## Scope

Resolve signature/type information for supported callable objects and partials
in [injected_route.py](../fastapi_canon/injected_route.py). Retain public function
identity and operation naming. Avoid mutating caller-owned endpoint objects.

## Acceptance criteria

- [ ] Callable objects and partials resolve Inject and FromDishka parameters and keep those parameters out of OpenAPI.
- [ ] Cover async and sync callables, bound methods, partial arguments and future annotations on supported Python versions.
- [ ] Use real requests with request-scoped providers and cleanup assertions, not registration-only tests.
- [ ] Existing functions, @inject wrappers and explicit route_class behavior remain supported.
- [ ] Unsupported callable forms fail with a targeted setup message rather than an unrelated schema error.
- [ ] Check Python 3.12 as well as the reproduction's Python 3.14.

## Verification

Extend the injected-route unit/integration suites and operation-ID contract tests; exercise callable objects that have no injected parameters too.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
