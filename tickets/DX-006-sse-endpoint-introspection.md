# DX-006: Preserve the original SSE callable for operation lookup

- Status: In progress
- Priority: P2
- Kind: Verified bug
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: New review finding; known limitation of PR #77.

## Problem and reproduction

Run this with `uv run --frozen python`:

```python
from collections.abc import AsyncIterator
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature

router = APIRouter(namespace="demo")


@router.sse("/events")
async def events() -> AsyncIterator[str]:
    yield "hello"


composition = Composition(features=[Feature(name="demo", routers=[router])])
app = composition.build(FastAPI())
print(composition.inspect(app).operation(events))
```

Baseline: `LookupError: Expected one operation for endpoint, found 0`, even
without injection. HTTP callable lookup works.
Inspect [SSE registration](../fastapi_canon/api_router.py),
[introspection](../fastapi_canon/introspection.py) and
[lookup values](../fastapi_canon/diagnostics.py). This was also documented as a
known limitation in PR #77.

## Scope

Preserve declaration identity through SSE and injection wrappers. Keep the
snapshot detached and avoid placing Python callable metadata in exported schemas.

## Acceptance criteria

- [ ] operation(events) returns the correct SSE HTTP operation with and without injected parameters.
- [ ] Nested inclusion and effective prefixes preserve the original callable reference.
- [ ] Explicit operation IDs and operation_by_id() keep working; ambiguous callable lookups still raise.
- [ ] Inspection never executes the generator or dependencies.
- [ ] OpenAPI/JSON exports contain no implementation metadata, and ordinary HTTP lookup is unchanged.

## Verification

Extend the real introspection and APIRouter suites with finite SSE generators and exact feature/path/operation assertions.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
