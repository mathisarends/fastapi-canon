# Build with Canon

Start with [From Python to typed clients](quickstart.md). It builds HTTP, SSE
and WebSocket declarations with shared errors and separate client generators.

| I want to… | Guide |
| --- | --- |
| Define public errors | [Shared problems](problems.md) |
| Check error declarations in CI | [Static error checks](error-checking.md) |
| Upload files, send multipart forms and download binary content | [File transfers](integrations/uploads.md) |
| Send and consume typed SSE events | [Streaming](integrations/sse/README.md) |
| Build WebSocket RPC and message streams | [WebSockets](integrations/websockets/README.md) |
| Generate and configure clients | [Client generation](codegen.md) |
| Wire features, dependencies and lifecycles | [Application composition](composition.md) |
| Trace application services | [Instrumentation](telemetry.md) |
| Correlate HTTP logs and traces | [Request logging](request-logging.md) |
| Manage database sessions and migrations | [Database resources](database.md) |

## Optional integrations

[WebRTC](integrations/webrtc/README.md) adds media sessions and typed browser
contracts. [MCP](integrations/mcp/README.md) mounts FastMCP features with Dishka.
Choose these when your application needs them.

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
