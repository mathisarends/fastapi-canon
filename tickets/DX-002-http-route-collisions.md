# DX-002: Reject ambiguous HTTP method and path contributions

- Status: In progress
- Priority: P1
- Kind: Verified bug
- Baseline: `a366702` (2026-11-05)
- Depends on: None
- Earlier review: New review finding; extends C3.

## Problem and reproduction

Run this with `uv run --frozen python`:

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature

one = APIRouter(namespace="one")
two = APIRouter(namespace="two")


@one.get("/same")
async def first() -> str:
    return "one"


@two.get("/same")
async def second() -> str:
    return "two"


composition = Composition(
    features=[
        Feature(name="one", routers=[one]),
        Feature(name="two", routers=[two]),
    ]
)
app = FastAPI()
print(composition.check(app).has_errors)
composition.build(app)
print(app.openapi()["paths"]["/same"]["get"]["operationId"])
```

Baseline: `False`, then `two.second`. Both routes are accepted despite having
the same HTTP method/path. Distinct operation IDs do not prevent shadowing.

## Scope

Add a composition-boundary check alongside [operation checks](../fastapi_canon/feature.py).
Use effective paths after inclusion. Define equivalent parameter names and converter
behavior explicitly. Keep HTTP and WebSocket namespaces separate.

## Acceptance criteria

- [ ] check() reports an error with both feature owners and both method/path locations; build() rejects before installing contributions.
- [ ] Check collisions between features, nested routers and existing application routes, including schema-hidden routes.
- [ ] Cover /items/{item_id} versus /items/{other_id}; document converter equivalence without trying to prove arbitrary path overlap.
- [ ] Distinct methods on the same path and distinct effective prefixes remain valid; do not reject intentional static-versus-parameter routing merely for overlap.
- [ ] Preserve the existing operation-ID diagnostic and add a stable diagnostic for route ambiguity.
- [ ] Treat stricter accepted configurations as a breaking change and document migration.

## Verification

Use real ASGI requests to show that valid routes remain reachable; verify failed preflight leaves the application unmodified. Run feature-check, introspection and router-contract suites.

Apply the [shared completion and release rules](README.md#shared-acceptance-and-verification-rules).

## Completion

- Implementation or decision PR:
- Final commit and verification:
- Remaining follow-ups:
