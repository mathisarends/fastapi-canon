# Documentation

Read the [hardening stack and SDK migration guide](hardening-overview.md) for all
15 ticket PRs, changed defaults, and integration steps.

The README explains why fastapi_canon.integrations.websockets exists and gets a first service running. These
guides cover the individual parts of the API in more detail:

- [Services and channels](services.md) â€” define methods and assemble endpoints
- [JSON-RPC protocol](protocol.md) â€” request cancellation and wire conventions
- [Dependency injection](dependencies.md) â€” pass application services into handlers
- [Connections and events](connections-and-events.md) â€” inspect connections and push updates
- [Typed errors](errors.md) â€” make expected failures part of the contract
- [Message streams](message-streams.md) — typed results with open, completion and cancellation
- [Binary streams](streams.md) â€” stream bytes on dedicated endpoints
- [Sockets](sockets.md) â€” proxy native WebSocket protocols such as CDP and declare their protocol
- [Observability](observability.md) â€” instrument calls and streams with middleware and OpenTelemetry
- [Contract and clients](clients.md) â€” export OpenRPC and generate typed clients
- [Transports](transports.md) â€” serve with FastAPI, a custom adapter, or in-memory tests
- [Releasing](releasing.md) â€” prepare and verify the unified Canon release

Start with services and channels if you are building a server. Start with the
contract and client guide if the server definition already exists.

The stable import surface is `fastapi_canon.integrations.websockets` plus the documented integration
the package-root FastAPI API, `fastapi_canon.integrations.websockets.adapters.dishka`, and
`fastapi_canon.integrations.websockets.adapters.opentelemetry`, plus `fastapi_canon.integrations.websockets.testing` and `fastapi_canon.integrations.websockets.codegen`.
Other submodules are implementation details.
