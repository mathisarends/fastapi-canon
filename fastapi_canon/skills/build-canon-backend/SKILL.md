---
name: build-canon-backend
description: Build, extend or review FastAPI Canon applications with feature slices, Composition and Dishka wiring, typed contracts, resource scopes and generated SDKs. Use for Canon consumers, not library maintenance or plain FastAPI projects without Canon.
---

# Build a Canon backend

Build business features as ordinary Python, expose typed contracts through Canon,
and assemble them explicitly with `Feature` and `Composition`.

## Start with the actual project

1. Read the application's instructions, dependency pins and architecture policy.
   Use its architecture tooling when configured; do not assume Smelt is installed.
2. Run `uv run fastapi-canon --version` and verify the APIs in that environment.
   The bundled examples accompany the installed library. Linked GitHub guides
   track development; use the matching release tag when consulting them for an
   older installation. The Canon source checkout is not required.
3. Read only the reference needed for the current decision:

| Task | Load |
| --- | --- |
| Understand the components or assemble the app | [Components and composition](references/components.md) |
| Choose folders, ownership, imports or feature boundaries | [Application layout](references/application-layout.md) |
| Implement a route, service, port, provider or error mapping | [HTTP feature walkthrough](references/http-feature.md) |
| Add persistence, transactions, workers or pooled clients | [Resources and scopes](references/resources.md) |
| Add streaming/RPC, SDK generation or telemetry | [Transports and SDKs](references/transports-and-sdk.md) |
| Choose test boundaries and verify a feature | [Testing and verification](references/testing.md) |

Load only the needed reference; follow deeper links when that decision requires
them. Supporting references are installed beside this file for both agent tools.

## Keep these boundaries

- Domain owns rules and plain exceptions. Application owns workflows and ports.
  Infrastructure implements I/O. Presentation maps wire models and public errors.
  Both outer layers depend inward; `feature.py` and the app root assemble them.
- Give each feature one `fastapi_canon.APIRouter` for HTTP, typed SSE
  (`@router.sse`), JSON-RPC channels (`router.include_channel`) and WebRTC
  (`@router.webrtc`). Contribute it once through `Feature(routers=[router])`.
- Use Dishka for application dependencies. Put `DishkaRoute` on the router for
  HTTP handlers using `FromDishka`; decorate SSE endpoints with `@inject`; mount
  RPC methods request services with `Inject[T]`. Let `Composition` own the
  container and scope lifecycle.
- Import Canon through `fastapi_canon`, `fastapi_canon.error` or documented
  `fastapi_canon.integrations.*` facades. Define each public problem once, bind
  its registry to the router, and declare operation failures with `raises=`.
- Use `Composition(features=(...))`; composition arguments are keyword-only.
  Give operations stable explicit IDs and concrete input/output models.
- Keep request resources out of streams and background tasks. Load the resource
  reference before changing ownership or transaction boundaries.

## Apply and verify

Create only modules with real responsibilities; prefer subject modules over
technical `services/` and `use_cases/` folders. Verify behavior and exported
contracts with the application's required gates. Regenerate
clients when contracts change and report the checks actually executed.
