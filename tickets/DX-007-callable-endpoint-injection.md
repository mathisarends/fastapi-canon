# DX-007: Support injected callable objects and partial endpoints

- Status: Done
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

Verified against the DX-006 parent: Python 3.14 reports FastAPIError with
`Invalid args for response field` for both cases. Python 3.12 reports TypeError
that the instance/partial is not a module, class, method or function. After the
fix, both exact examples register on both interpreters. Real request/cleanup
tests additionally cover future annotations and class-local inherited aliases.

## Scope

Resolve signature/type information for supported callable objects and partials
in [injected_route.py](../fastapi_canon/injected_route.py). Retain public function
identity and operation naming. Avoid mutating caller-owned endpoint objects.

## Acceptance criteria

- [x] Callable objects and partials resolve Inject and FromDishka parameters and keep those parameters out of OpenAPI.
- [x] Cover async and sync callables, bound methods, partial arguments and future annotations on supported Python versions.
- [x] Use real requests with request-scoped providers and cleanup assertions, not registration-only tests.
- [x] Existing functions, @inject wrappers and explicit route_class behavior remain supported.
- [x] Unsupported callable forms fail with a targeted setup message rather than an unrelated schema error.
- [x] Check Python 3.12 as well as the reproduction's Python 3.14.

## Verification

Extend the injected-route unit/integration suites and operation-ID contract tests; exercise callable objects that have no injected parameters too.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#87](https://github.com/mathisarends/fastapi-canon/pull/87).
- Final commit and verification: implementation `f933f76`, merged current
  `origin/main` after DX-006 landed. Focused injection, introspection and
  operation-ID suites: 86 passed on both Python 3.12 and 3.14. Full root pytest:
  2,510 passed, 26 skipped, 153 warnings on Windows/Python 3.14; three additional
  constructor/bound-positional cases passed in the final focused runs.
  Ruff, changed/full Smelt, all-files pre-commit and the full type gate passed,
  including the installed-wheel RPC completeness check (94.8%). The runnable
  documentation example and exact ticket reproduction passed. Standalone
  browser and minimum-dependency matrices were not repeated for this adapter.
  Merged into main as `13099ff`.
- Remaining follow-ups: none in scope. Opaque or variadic non-function callables
  require a typed named-parameter wrapper; bind positional-only arguments first.
  Constructor injection requires a callable instance or typed function. These
  setup constraints and migration steps are documented for the next minor release.
