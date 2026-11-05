# DX-001: Keep provider-factory reassignment analysis sound

- Status: In progress
- Priority: P1
- Kind: Verified bug
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: New review finding; PR #78.

## Problem and reproduction

Save this as `build/ticket-repros/DX-001/src/api.py` (create the directory first):

```python
from typing import Protocol
from dishka import Provider, Scope, provide
from fastapi_canon import APIRouter, Inject


class Repository(Protocol):
    async def read(self) -> str: ...


class Missing(Exception):
    pass


class SafeRepository:
    async def read(self) -> str:
        return "ok"


class FailingRepository:
    async def read(self) -> str:
        raise Missing()


class Bindings(Provider):
    @provide(scope=Scope.REQUEST)
    def repository(self) -> Repository:
        impl = SafeRepository()
        impl = FailingRepository()
        return impl


router = APIRouter()


@router.get("/read")
async def read(repository: Inject[Repository]) -> str:
    return await repository.read()
```

Run `uv run --frozen fastapi-canon check errors build/ticket-repros/DX-001/src --strict --require-complete --format json`.
At the baseline this returns exit 0, zero gaps and zero incomplete operations.
Replacing the factory body with `return FailingRepository()` correctly reports
`CAN002` and exits 1. Both cases return the same implementation at runtime.
The reverse AST traversal overwrites the final assignment with an earlier one.

## Scope

Limit this change to identifying implementations returned or yielded by provider
factories in [source.py](../fastapi_canon/checks/source.py), with observable CLI
regressions in [the port suite](../tests/unit/checks/test_analysis_ports.py).
Do not expand unrelated Python flow analysis.

## Acceptance criteria

- [x] The exact repro cannot report complete success: report the Missing gap when the implementation is known, or CAN003 when it is ambiguous.
- [x] Cover straight-line reassignment, annotated assignment, conditional assignments and return/yield positions; unreachable assignments after a return cannot replace its value.
- [x] Do not collect unrelated constructors from nested functions/classes as factory results.
- [x] Direct factories, provides= bindings, aliases and override-provider unions retain their supported behavior.
- [x] Classify any stricter validation or changed accepted behavior under the repository release policy; add concrete migration notes when required.

## Verification

Run the repro and its direct-return control through the public CLI; run `uv run --frozen pytest tests/unit/checks`.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR: [#81](https://github.com/mathisarends/fastapi-canon/pull/81).
- Final commit and verification: source fix `6435c50d91da7572e4c22228a538227e5c57fd37`
  (regressions introduced in `369b44a`). The exact reassignment repro and its
  direct-return control both exit 1 with CAN002 after the fix.
  `uv run --frozen pytest tests/unit/checks -q`: 117 passed.
  `uv run --frozen pytest`: 2,416 passed, 26 skipped, 153 warnings on
  Windows/Python 3.14 (the additional module-alias case passed in the focused run).
  Mypy, Ruff lint/format, changed Smelt and full pre-commit including full Smelt
  passed. The skipped database cases require their optional external services.
  The standalone distribution/browser matrices were not run for this static
  checker-only change. Status stays In progress until the implementation lands.
- Remaining follow-ups: broader checker flow slices remain DX-014 through DX-016;
  unmodeled provider control flow reports CAN003.
