# fastapi-canon

[![PyPI](https://img.shields.io/pypi/v/fastapi-canon?color=blue)](https://pypi.org/project/fastapi-canon/)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-canon)](https://pypi.org/project/fastapi-canon/)
[![CI](https://github.com/mathisarends/fastapi-canon/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mathisarends/fastapi-canon/actions/workflows/ci.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2a6db2)](https://mypy-lang.org/)
[![License](https://img.shields.io/pypi/l/fastapi-canon)](LICENSE)

**FastAPI for streams, WebSockets and AI apps, with typed clients.**

FastAPI is great at JSON endpoints. WebSockets, streams and their errors are
left to you, and none of it shows up in OpenAPI. Canon makes them typed
contracts and generates Python and TypeScript clients.

**Plain FastAPI:** your own protocol, dispatch and error format, no schema.

```python
@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    while True:
        message = await websocket.receive_json()
        if message.get("method") != "tasks.complete":
            await websocket.send_json({"id": message.get("id"), "error": "unknown"})
            continue
        try:
            task = await service.complete(int(message["params"]["task_id"]))
            await websocket.send_json(
                {"id": message["id"], "result": task.model_dump()}
            )
        except TaskMissing:
            await websocket.send_json({"id": message["id"], "error": "not found"})
```

**Canon:** a typed method with a declared error.

```python
tasks = RpcChannel(namespace="tasks")


@tasks.method(raises=[task_missing])
async def complete(task_id: int, service: Inject[TaskService]) -> Task:
    return await service.complete(task_id)


router.include_channel(tasks, path="/ws")
```

The generated client is one call: `task = await client.tasks.complete(task_id=1)`.

## What Canon adds

- **Errors, declared once.** Raise plain exceptions; get the response, OpenAPI
  entry and client error from one `Error`.
- **WebSocket RPC.** Typed methods, streams and events, no request-ID plumbing.
- **Typed SSE.** Clients iterate typed events and know when a stream failed.
- **Everything in the contract.** HTTP, streams, sockets and WebRTC, checked in CI.
- **One app model.** Features, Dishka dependencies and lifecycles for every transport.

## Installation

```console
uv add fastapi-canon
```

Python 3.12–3.14. Extras: `sse`, `websockets`, `webrtc`, `mcp`, `codegen`.
Upgrading? See the [changelog](CHANGELOG.md) and [upgrade guide](docs/24-upgrading.md).

## Learn Canon

The [docs](docs/README.md) are a learning path; start with the
[quickstart](docs/01-quickstart.md).

| Step | Guide | You learn |
| --- | --- | --- |
| 1 | [Quickstart](docs/01-quickstart.md) | HTTP, SSE and RPC with one error, plus generated clients |
| 2 | [Errors](docs/02-errors.md) | Public errors, fields and headers |
| 3 | [Streaming](docs/03-streaming.md) | Typed SSE, POST streams, event unions |
| 4 | [WebSockets](docs/04-websockets.md) | Methods, streams and events |
| 5 | [Composition](docs/05-composition.md) | Features, Dishka services, policies, tests |
| 6 | [Client generation](docs/06-codegen.md) | Generating clients and catching drift |
| 7 | [Error checking](docs/07-error-checking.md) | Finding undeclared errors before runtime |
| 8–10 | [Files](docs/08-file-transfers.md), [database](docs/09-database.md), [observability](docs/10-observability.md) | Production concerns |
| 11–12 | [WebRTC](docs/11-webrtc.md), [MCP](docs/12-mcp.md) | Media sessions and agent tools |

Runnable apps are in [`examples/`](examples/README.md). Coding agents can install
the [bundled backend skill](fastapi_canon/skills/build-canon-backend/SKILL.md)
with `uv run fastapi-canon skill install`.
