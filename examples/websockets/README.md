# Examples

Applications mount JSON-RPC channels on their feature router with
`APIRouter.include_channel`, as [`authenticated.py`](authenticated.py) does:

```python
router = APIRouter()
router.include_channel(channel, path="/rpc", context=open_session, resolver=...)
```

The other examples work one level lower, on the `fastapi_canon.integrations.websockets`
protocol model: channels, frozen protocols, schemas, binary streams and raw sockets.
Each file is standalone and executable.

- [`authenticated.py`](authenticated.py) authenticates the WebSocket handshake,
  binds the login to the Dishka connection scope and closes the connection when
  the credentials expire.
- [`basic.py`](basic.py) declares a typed method, serves one raw JSON-RPC request,
  and prints the response.
- [`features.py`](features.py) mounts multiple channels on one versioned
  service.
- [`notifications.py`](notifications.py) decorates an injected async event
  source and declares its typed payload.
- [`schemas.py`](schemas.py) renders JSON Schema and OpenRPC documents from the
  same protocol definition.
- [`generated_clients`](generated_clients) contains inspectable Python and
  TypeScript clients generated from
  [`automation.py`](generated_clients/automation.py). That module declares
  server methods, an event, binary streams, and a raw socket, and exports the
  contract that becomes
  [`automation.openrpc.json`](generated_clients/automation.openrpc.json). The
  binary streams generate
  [`streams.py`](generated_clients/python/automation_client/streams.py) and
  [`streams.ts`](generated_clients/typescript/streams.ts). Regenerate the
  contract and both clients from this directory's
  [`rpcgen.toml`](generated_clients/rpcgen.toml):

  ```bash
  uv run python -m fastapi_canon.integrations.websockets.codegen.cli generate \
    --config examples/websockets/generated_clients/rpcgen.toml
  ```
- [`socket_protocol`](socket_protocol/README.md) declares a small CDP-shaped
  protocol on a socket and plugs typed Python and TypeScript clients for it
  into the generators.
- [`typed_socket`](typed_socket/README.md) declares an annotated message handler,
  validates incoming and outgoing JSON with Pydantic, and includes generated
  Python and TypeScript clients exercised against a live FastAPI server.

Run an example from the repository root:

```bash
uv run python examples/websockets/basic.py
uv run python examples/websockets/features.py
uv run python examples/websockets/notifications.py
uv run python examples/websockets/schemas.py
```
