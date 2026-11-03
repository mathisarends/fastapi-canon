---
name: build-canon-backend
description: Build, extend or review a clean FastAPI application with fastapi-canon. Use for feature slices, backend folder structure, Dishka wiring, typed HTTP/error contracts, resource scopes and generated SDKs. Distinguish application architecture from changes to the Canon library itself.
---

# Build a Canon backend

Build business features as ordinary Python, expose typed contracts through Canon,
and assemble them explicitly with `Feature` and `Composition`.

## Start with the actual project

1. Read its `AGENTS.md`, dependency pins and architecture policy. In this
   repository, read `smelt.yml` and `docs/architecture.md` and run
   `uv run --frozen smelt context <module-or-path>` before implementation.
2. Establish whether the task changes a **consumer application** or **Canon
   itself**. The tree in the layout reference is the application target. Keep
   the library's existing module boundaries when working on `fastapi_canon/`.
3. Verify the installed API against `pyproject.toml`, `docs/canon.md` and the
   relevant guide/source. These examples target the 0.13 API on `origin/main`;
   a design decision or an older consumer is not evidence of an available API.
4. Read only the reference needed for the current decision:

| Task | Load |
| --- | --- |
| Choose folders, ownership, imports or feature boundaries | [Application layout](references/application-layout.md) |
| Implement a route, service, port, provider or error mapping | [HTTP feature walkthrough](references/http-feature.md) |
| Add persistence, transactions, workers or pooled clients | [Resources and scopes](references/resources.md) |
| Add streaming/RPC, SDK generation or telemetry | [Transports and SDKs](references/transports-and-sdk.md) |
| Choose test boundaries and verify a feature | [Testing and verification](references/testing.md) |

`docs/...` and `examples/...` refer to the Canon repository root; reference links
are relative to this skill. Elsewhere, use the docs for the pinned Canon version.

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
contracts with the project's required gates (`AGENTS.md` here). Regenerate
clients when contracts change and report the checks actually executed.
