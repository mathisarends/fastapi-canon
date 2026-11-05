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

- [x] operation(events) returns the correct SSE HTTP operation with and without injected parameters.
- [x] Nested inclusion and effective prefixes preserve the original callable reference.
- [x] Explicit operation IDs and operation_by_id() keep working; ambiguous callable lookups still raise.
- [x] Inspection never executes the generator or dependencies.
- [x] OpenAPI/JSON exports contain no implementation metadata, and ordinary HTTP lookup is unchanged.

## Verification

Extend the real introspection and APIRouter suites with finite SSE generators and exact feature/path/operation assertions.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#86](https://github.com/mathisarends/fastapi-canon/pull/86).
- Final commit and verification: source `67efea2`, synchronized with merged
  DX-005/main in `cc59674`. The exact repro now resolves demo.events.
  Focused introspection/injection/router/operation-ID suites: 78 passed.
  Full root pytest: 2,488 passed, 26 skipped, 153 warnings on Windows/Python 3.14;
  PostgreSQL cases need an external database. Client gate passed exact generator
  drift, TypeScript typechecks and runtime/coverage checks. Mypy, Ruff, changed
  Smelt and full pre-commit passed. Standalone wheel/browser matrices were not
  repeated for the metadata-only change. Status stays In progress until merge.
- Remaining follow-ups: none in this identity-lookup scope; callable-object
  registration/injection remains DX-007.
