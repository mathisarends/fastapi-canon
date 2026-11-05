# MCP and Dishka

Install `fastapi-canon[mcp]` to expose explicitly selected feature contributions
through one FastMCP HTTP endpoint. Core imports and `McpOptions` remain usable
without FastMCP. This integration targets FastMCP 4.1.3 through the latest 4.x
and selects Pydantic >=2.12; core installations retain their Pydantic >=2.9 minimum.

```python
from dishka import Provider, Scope, provide
from fastapi import FastAPI

from fastapi_canon import Composition, Feature
from fastapi_canon.integrations.mcp import Inject, McpMount, McpOptions, McpServer


class PortfolioService:
    async def get(self, portfolio_id: str) -> dict[str, str]:
        return {"id": portfolio_id}


class PortfolioProvider(Provider):
    service = provide(PortfolioService, scope=Scope.REQUEST)


portfolio_mcp = McpServer("Portfolio")


@portfolio_mcp.tool
async def get_portfolio(
    portfolio_id: str,
    service: Inject[PortfolioService],
) -> dict[str, str]:
    """Load a portfolio."""
    return await service.get(portfolio_id)


portfolio_feature = Feature(
    name="portfolio",
    providers=[PortfolioProvider],
    mcp=[McpMount(server=portfolio_mcp, namespace="portfolio")],
)

app = Composition(
    features=[portfolio_feature],
    mcp=McpOptions(),
).apply(FastAPI(title="Maximus"))
```

The endpoint is `/mcp`; its server name defaults to `FastAPI.title` (`Maximus`).
Clients see `portfolio_get_portfolio` with only `portfolio_id` in its input schema.
`McpOptions(name="Agent API", path="/agent")` overrides the server identity and
endpoint. Namespaces are explicit public API identifiers, independent of feature
names. Duplicate namespaces or server instances fail during composition.
Contributing mounts requires explicit `Composition(mcp=McpOptions())` activation.

Configure the common root with `McpOptions(stateless_http=True,
mask_error_details=True, middleware=(audit_middleware,))` when replacing a
custom FastMCP HTTP adapter. Both boolean options default to `None`, which
preserves FastMCP's settings and environment configuration. Root middleware
runs in declaration order inside Canon's execution scope and outside child
server middleware. Each application creates its own root; use independent
middleware instances when they retain application-specific state. Stateful
HTTP requires session affinity in deployments with multiple workers. Stateless
HTTP does not make application-owned capabilities or deduplicated results shared.
FastMCP error masking should also be enabled on child servers when their own
error handling could expose details; the root option does not rewrite children.

The combined catalog also rejects duplicate public component identities during
discovery and before execution. For example, namespace `a` with tool `b_c` and
namespace `a_b` with tool `c` both produce `a_b_c`. Rename one local component or
namespace. Distinct component versions remain valid. These checks run against
the current caller's live catalog, including authenticated dynamic providers;
composition preflight does not start or query those providers.

`McpServer` is a thin FastMCP subclass: it translates `Inject[T]` annotations before
FastMCP creates schemas. Its `tool`, `resource`, and `prompt` decorators retain
FastMCP behavior. Async injected handlers and direct `add_tool(function)` are
supported. A plain `FastMCP` server is also accepted by `McpMount`; its functions
use FastMCP's native dependencies rather than Canon's `Inject[T]` annotations.

Composition creates a separate MCP root for each application. Its middleware
borrows the application's APP container and opens one REQUEST scope for each tool,
resource or prompt execution. All injected parameters share that scope. Cleanup
runs after result conversion, including exceptions and cancellation. The MCP
lifespan runs inside the composition lifecycle; MCP does not close the borrowed
APP container. Child server declarations do not store application containers.

FastAPI router dependencies and `RouterPolicy` do not protect mounted MCP routes.
Pass a native FastMCP auth provider through `McpOptions(auth=...)` and enforce
domain authorization in shared application services. Auth configured on a child
server is rejected because its HTTP authentication does not protect the common
endpoint. HTTP request-bound providers are not portable MCP
dependencies. No authentication identity or tenant is inferred from tool arguments.

This first integration uses FastMCP's native error handling. It does not add a
Canon problem binding or generate MCP clients. Injected sync handlers, deferred
generator results, background tasks and worker bootstrap are outside this
version's support. `McpServer` rejects `task=True` in its component decorators explicitly.
