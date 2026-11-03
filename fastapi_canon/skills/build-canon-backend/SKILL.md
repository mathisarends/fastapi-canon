---
name: build-canon-backend
description: Build, extend or review an application using the installed fastapi-canon library. Use for backend folder structure, feature slices, Dishka wiring, typed HTTP/error contracts, resource scopes and generated SDKs. Intended for Canon consumers, not library maintenance.
---

# Build a Canon backend

Build business features as ordinary Python, expose typed contracts through Canon,
and assemble them explicitly with `Feature` and `Composition`.

## Start with the actual project

1. Read the application's instructions, dependency pins and architecture policy.
   Use its architecture tooling when configured; do not assume Smelt is installed.
2. Check the installed Canon version and use its matching documentation. These
   examples and linked guides target Canon 0.13.0; verify APIs before using them
   with another version. The Canon source checkout is not required.
3. Read only the reference needed for the current decision:

| Task | Load |
| --- | --- |
| Choose folders, ownership, imports or feature boundaries | [Application layout](references/application-layout.md) |
| Implement a route, service, port, provider or error mapping | [HTTP feature walkthrough](references/http-feature.md) |
| Add persistence, transactions, workers or pooled clients | [Resources and scopes](references/resources.md) |
| Add streaming/RPC, SDK generation or telemetry | [Transports and SDKs](references/transports-and-sdk.md) |
| Choose test boundaries and verify a feature | [Testing and verification](references/testing.md) |

Supporting references are bundled with this skill. Links to detailed Canon
guides open public documentation for the example version.

## Keep these boundaries

- Domain owns rules and plain exceptions. Application owns workflows and ports.
  Infrastructure implements I/O. Presentation maps wire models and public errors.
  Both outer layers depend inward; `feature.py` and the app root assemble them.
- Use Dishka for application dependencies. Put `DishkaRoute` on the router that
  declares HTTP handlers using `FromDishka`. Let `Composition` own the container
  and HTTP scope lifecycle.
- Import Canon through `fastapi_canon`, `fastapi_canon.error` or documented
  `fastapi_canon.integrations.*` facades. Define each public problem once, bind
  its registry to the router, and declare operation failures with `raises=`.
- Use `Composition(features=(...))`; 0.13 composition arguments are keyword-only.
  Give operations stable explicit IDs and concrete input/output models.
- Keep request resources out of streams and background tasks. Load the resource
  reference before changing ownership or transaction boundaries.

## Apply and verify

Create only modules with real responsibilities; prefer subject modules over
technical `services/` and `use_cases/` folders. Verify behavior and exported
contracts with the application's required gates. Regenerate
clients when contracts change and report the checks actually executed.
