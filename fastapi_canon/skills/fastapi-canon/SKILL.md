---
name: fastapi-canon
description: Build, extend or review FastAPI applications that use fastapi-canon — feature slices, Composition and Dishka wiring, typed HTTP/SSE/WebSocket RPC/WebRTC contracts, Problem Details errors, database scopes and generated SDKs. Use in Canon consumer projects, not for maintaining the Canon library or plain FastAPI apps.
---

# FastAPI Canon

Write business features as plain Python, expose them through typed Canon routers
and assemble them explicitly with `Feature` and `Composition`.

## Before you change code

1. Follow the project's own instructions, pins and architecture tooling.
2. Run `uv run fastapi-canon --version`. This skill matches the installed
   version; check signatures in that environment before using an option.
3. Load only the reference for the current decision:

| Task | Reference |
| --- | --- |
| Find where code belongs | [layout.md](references/layout.md) |
| Wire features, errors, telemetry or the app | [composition.md](references/composition.md) |
| Add a route, service, port, provider or error | [http-feature.md](references/http-feature.md) |
| Add persistence, transactions or background work | [resources.md](references/resources.md) |
| Add SSE, WebSocket RPC, WebRTC, MCP or SDKs | [transports.md](references/transports.md) |
| Test and verify a change | [testing.md](references/testing.md) |

## Rules

- Domain owns rules and plain exceptions; application owns workflows and ports;
  infrastructure implements I/O; presentation owns wire models and `Error`s.
  `feature.py` and the app root assemble them.
- One `fastapi_canon.APIRouter(namespace=...)` per feature carries HTTP, SSE
  (`response_class=EventSourceResponse`) and RPC (`router.include_channel`).
  WebRTC uses `WebRtcRouter`. Contribute both via `Feature(routers=[...])`.
- Endpoints, streams and RPC methods request services with `Inject[T]`. Only
  FastAPI dependency functions using `FromDishka[T]` need `@inject`.
  `Composition` owns the container: no `setup_dishka`, no manual registry.
- Define each public `Error` once and declare it with `raises=[...]`.
- Import from `fastapi_canon`, `fastapi_canon.error` and
  `fastapi_canon.integrations.*` only.
- Streams, sockets and background tasks never hold request resources; they
  inject APP services or a `Scoped` factory.
- Keep published operation IDs stable; pin them with `operation_id=` on rename.

## Finish

Run the project's gates plus `fastapi-canon check composition` and
`fastapi-canon check errors` ([testing.md](references/testing.md)). Regenerate
clients when contracts change. Report only the checks you actually ran.
