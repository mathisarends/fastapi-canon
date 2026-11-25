# Build with Canon

Each level builds on the previous one. Optional integrations come last.

| Level | Guide | You learn |
| --- | --- | --- |
| 1 | [From Python to typed clients](quickstart.md) | HTTP, SSE and RPC with one error and generated clients |
| 2 | [Shared problems](problems.md) | Public errors, extensions and headers |
| 3 | [Streaming](integrations/sse/README.md) | Event unions, admission checks and POST streams |
| 4 | [WebSockets](integrations/websockets/README.md) | RPC methods, message streams and notifications |
| 5 | [Application composition](composition.md) | Features, Dishka services, lifecycles and router policies |
| 6 | [Client generation](codegen.md) | Generator configuration and drift checks in CI |
| 7 | [Static error checks](error-checking.md) | Finding undeclared errors before runtime |
| 8 | [File transfers](integrations/uploads.md) | Uploads, multipart forms and binary downloads |
| 9 | [Database resources](database.md) | Sessions, transactions and migrations |
| 10 | [Instrumentation](telemetry.md) | Tracing services and correlating [request logs](request-logging.md) |
| ★ | [WebRTC](integrations/webrtc/README.md), [MCP](integrations/mcp/README.md) | Media sessions; tools for agents |

## Reference and upgrades

- [Public imports](canon.md), [router contracts](routers.md) and [contract exports](contracts.md)
- [Composition diagnostics](system-checks.md) and [diagnostic codes](diagnostics.md)
- [Installation and upgrades](package-migration.md), [WebSocket migration](integrations/websockets/migration.md)
  and [tested compatibility](compatibility.md)
- [Runnable examples](../examples/README.md)

## Contributing

[Commands](contributing.md), [architecture](architecture.md),
[guardrails](architecture-guardrails.md) and [release procedures](releases.md)
describe development of Canon itself.
