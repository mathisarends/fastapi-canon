# Transports

All transports reuse the feature's services and `Error` definitions.

```python
@router.get("/events", response_class=EventSourceResponse, raises=[PROJECT_MISSING])
async def events(projects: Inject[ProjectService]) -> AsyncIterator[ProjectEvent]:
    async for event in projects.events():
        yield event  # projects.events() can raise ProjectMissing after yielding


channel = RpcChannel(namespace="projects")  # WebSocket JSON-RPC


@channel.method("rename", raises=[PROJECT_MISSING])
async def rename(
    project_id: int, title: str, projects: Inject[ProjectService]
) -> ProjectResponse: ...


router.include_channel(channel, path="/rpc")  # after declaring methods
```

- SSE runs after the response starts. Inject APP-scoped services or use an
  APP-scoped `ServiceScope` to open short scopes. Let declared errors propagate
  so Canon sends the terminal `canon.problem` event.
- If an RPC method is missing, run `fastapi-canon inspect backend.app:create_app --rpc`
  to see the methods captured at each mount.
- WebRTC: `WebRtcRouter` with `@rtc.session(...)` in `Feature(routers=[...])`.
- MCP: `McpServer` + `McpMount` on the feature, `Mcp()` on the composition.
- Clients: configure `[codegen]` in `fastapi-canon.toml`, run
  `fastapi-canon generate`; CI runs `generate --check --diff`.

Guides: [streaming](https://github.com/mathisarends/fastapi-canon/blob/main/docs/03-streaming.md),
[WebSockets](https://github.com/mathisarends/fastapi-canon/blob/main/docs/04-websockets.md),
[codegen](https://github.com/mathisarends/fastapi-canon/blob/main/docs/06-codegen.md),
[WebRTC](https://github.com/mathisarends/fastapi-canon/blob/main/docs/11-webrtc.md),
[MCP](https://github.com/mathisarends/fastapi-canon/blob/main/docs/12-mcp.md).
