# fastapi-canon

**Typed APIs, streams, and clients for FastAPI.**

Define operations and public errors in Python. Generate Python and TypeScript
clients for HTTP, SSE, and WebSockets.

Built from the recurring problems my coding agents and I ran into while building
FastAPI applications: streaming responses, inconsistent errors, duplicated client
code, and application wiring.

Streaming tokens is straightforward—until you need typed events, predictable
failures, and a client that knows when the stream actually finished.

## Define it in Python. Use it from your client.

Declare a typed stream on Canon's `APIRouter`:

```python
from collections.abc import AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_canon import APIRouter, Composition, Feature


class Token(BaseModel):
    text: str


router = APIRouter()


@router.sse("/tokens", operation_id="generation.tokens")
async def tokens() -> AsyncIterator[Token]:
    yield Token(text="Hello")
    yield Token(text=" world")


app = Composition(
    features=(Feature(name="generation", routers=[router]),),
).build(FastAPI())
```

With a [codegen configuration](docs/quickstart.md#generate-and-use-the-sdk)
pointing at your app, generate both clients. For this SSE-only example, keep
only the OpenAPI source and use `sources = ["api"]` for both targets:

```console
uv run fastapi-canon generate
```

TypeScript:

```typescript
import { createClient } from "./generated";

const client = createClient({ baseUrl: "http://localhost:8000" });
try {
  for await (const token of client.generation.tokens()) {
    console.log(token.text);
  }
} finally {
  await client.close();
}
```

Python, inside an async function:

```python
from api_client import ApiClient

async with ApiClient("http://localhost:8000") as client:
    async for token in client.generation.tokens():
        print(token.text)
```

The event model becomes the client's item type. Canon handles framing, decoding
and explicit stream completion. Declared terminal problems raise `ProblemError`;
an unexpected EOF raises `TransportError`.

The same SDK can expose HTTP lookups and WebSocket RPC calls such as
`client.projects.get(...)` and `client.projects.cancel(...)`, with shared models
and public problem errors. See the [complete walkthrough](docs/quickstart.md).

## What Canon handles

- **HTTP, SSE and WebSockets:** typed operations, event streams and RPC methods,
  with generated Python and TypeScript clients.
- **Public errors:** map domain exceptions once, declare them with `raises=`,
  and carry the contract into HTTP RFC 9457 Problem Details, streams and SDKs.
- **Static checks and code generation:** find missing error mappings and
  declarations, report incomplete analysis, and catch generated-client drift in CI.
- **Application composition:** features contribute routers, Dishka providers and
  lifespans; composition owns installation and cleanup.
- **Instrumentation:** declare observed services and ports at the feature boundary
  without adding telemetry annotations to business logic.

Generated SDKs do not depend on Canon. Python clients use Pydantic, an HTTP client
and, for RPC, `websockets`; TypeScript clients use fetch/DOM APIs.
See [client generation](docs/unified-canon-concept.md) for supported operations
and target requirements.

## Install

```console
uv add "fastapi-canon[sse,websockets]"
uv add --dev "fastapi-canon[codegen]"
```

For HTTP-only applications, use `uv add fastapi-canon`. The base includes
FastAPI, Pydantic and Dishka. Python 3.12–3.14 is supported.

The current published release is **0.15.0**. See the
[upgrade guide](docs/package-migration.md#upgrade-to-0150) and
[tested compatibility](docs/compatibility.md). Upgrade servers and regenerated
clients together.

## Instrument application boundaries

Add the `otel` extra and declare the services or ports to observe:

```python
from fastapi_canon import Feature
from fastapi_canon.integrations.opentelemetry import instrument

tasks = Feature(
    name="tasks",
    routers=(router,),
    providers=(TaskProvider,),
    instrumentation=instrument(
        TaskRepository,
        TaskService,
        expected=(TaskNotFound,),
    ),
)
```

This excerpt uses your application's router, provider and service types.
Enable it with `Composition(..., telemetry=Telemetry(...))`. Canon observes
declared async methods through Dishka and adds port spans alongside HTTP
telemetry. Your application owns SDK providers and exporters.
See the [instrumentation guide](docs/telemetry.md) for a complete setup.

## Check contracts in CI

```console
uv run fastapi-canon check errors src --strict --require-complete
uv run fastapi-canon generate --check --diff
```

The static checker follows supported application calls and dependencies; it
reports unresolved paths rather than claiming complete coverage. Use it alongside
runtime tests. The generator checks exported contracts and client files for drift.

## Learn more

Start with the [quickstart](docs/quickstart.md), then choose a
[task guide](docs/README.md) or a [runnable example](examples/README.md).

Optional integrations also include [WebRTC media sessions](docs/integrations/webrtc/README.md),
[MCP](docs/integrations/mcp/README.md) and [database resources](docs/database.md).

For coding agents, install the bundled
[`build-canon-backend` skill](fastapi_canon/skills/build-canon-backend/SKILL.md)
from your application project:

```console
uv run fastapi-canon skill install
```

For updates and destination options, see
[the skill installation guide](docs/package-migration.md#agent-skill).
Contributor commands and repository architecture live in
[contributing](docs/contributing.md).
