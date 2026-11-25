# Canon docs

Work through 1–7 in order; pick 8–12 when you need them. Each page ends with a
link to the next.

## Learn

| | Guide | You learn |
| --- | --- | --- |
| 1 | [Quickstart](01-quickstart.md) | HTTP, SSE and RPC with one error, plus generated clients |
| 2 | [Errors](02-errors.md) | Public errors, fields, headers and what clients receive |
| 3 | [Streaming](03-streaming.md) | Typed SSE, the check-before-you-stream rule, POST streams |
| 4 | [WebSockets](04-websockets.md) | Methods, streams and events in a channel |
| 5 | [Composition](05-composition.md) | Features, Dishka services, policies and tests |
| 6 | [Client generation](06-codegen.md) | Generating clients and catching drift in CI |
| 7 | [Error checking](07-error-checking.md) | Finding undeclared errors before runtime |

## Build for production

| | Guide | You learn |
| --- | --- | --- |
| 8 | [File transfers](08-file-transfers.md) | Uploads, streaming bodies and downloads |
| 9 | [Database](09-database.md) | Transactions, short scopes and migrations |
| 10 | [Observability](10-observability.md) | Tracing and request logs |
| 11 | [WebRTC](11-webrtc.md) | Voice, video and data channels |
| 12 | [MCP](12-mcp.md) | Tools for AI agents |

## Look up

| | Reference |
| --- | --- |
| 20 | [Routers](20-routers.md): every router and response option |
| 21 | [Public API](21-public-api.md): import paths |
| 22 | [Contract export](22-contracts.md): OpenAPI, OpenRPC and WebRTC documents |
| 23 | [Checks and diagnostics](23-checks-and-diagnostics.md): preflight checks and codes |
| 24 | [Upgrading](24-upgrading.md): migration steps per release |
| 25 | [Compatibility](25-compatibility.md): tested dependency versions |

Transport details live in [`integrations/`](integrations): SSE, WebSockets and
WebRTC deep dives, numbered in reading order. Runnable apps are in
[`examples/`](../examples/README.md).

## Work on Canon

[90 Contributing](90-contributing.md) · [91 Architecture](91-architecture.md) ·
[92 Guardrails](92-architecture-guardrails.md) · [93 Releases](93-releases.md) ·
[94 Repository migration](94-repository-migration.md) · [95 RPC parity](95-rpc-parity.md)
