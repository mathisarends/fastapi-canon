---
name: fastapi-canon
description: Build, extend or review FastAPI applications that use fastapi-canon — feature slices, Composition and Dishka wiring, typed HTTP/SSE/WebSocket RPC/WebRTC contracts, Problem Details errors, database scopes and generated SDKs. Use in Canon consumer projects, not for maintaining the Canon library or plain FastAPI apps.
---

# FastAPI Canon

Features are plain Python, exposed through typed Canon routers and assembled
with `Feature` and `Composition`. This skill gives the overview; for details read
the installed source (`python -c "import fastapi_canon; print(fastapi_canon.__file__)"`)
and its docstrings. Public names are in `fastapi_canon/__init__.py` and
`fastapi_canon/integrations/*/__init__.py`.

| Task | Reference |
| --- | --- |
| Where code belongs | [layout.md](references/layout.md) |
| Route, service, provider, error, app wiring | [feature.md](references/feature.md) |
| Database, transactions, background work | [resources.md](references/resources.md) |
| SSE, WebSocket RPC, WebRTC, MCP, SDKs | [transports.md](references/transports.md) |
| Tests and checks | [testing.md](references/testing.md) |

## Rules

- One `APIRouter(namespace=...)` per feature for HTTP, SSE and RPC channels.
- Inject services with `Inject[T]`; `Composition` owns the Dishka container.
- Declare each public `Error` once and list it in `raises=[...]`.
- Streams, sockets and background tasks never use request resources.
- Keep published operation IDs stable (`operation_id=` when renaming).
- Finish with `fastapi-canon check composition` and `check errors`.
