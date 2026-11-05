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

For an application-owned operation that must outlive an HTTP response, register
`McpScope` at APP scope and enter it inside the operation's task:

```python
from dishka import AsyncContainer
from fastapi_canon.integrations.mcp import McpScope


class OperationProvider(Provider):
    @provide(scope=Scope.APP)
    def operation_scope(self, container: AsyncContainer) -> McpScope:
        return McpScope(container)


async def execute(scope: McpScope) -> dict[str, str]:
    async with scope() as scoped:
        service = await scoped.get(PortfolioService)
        return await service.get("p1")
```

Within that context, `Inject[T]` also resolves against the same container when
executing a registered native tool. Keep execution and result conversion inside
the context. Each entry owns a fresh REQUEST scope; nested and concurrent entries
restore the previous injection context on exit. The factory never closes the APP
container. Applications own their tasks, cancellation/shielding and logical-call
deduplication. Create and enter the operation scope inside a shielded task rather
than borrowing resources from its caller's transport scope. `McpScope` itself does
not require FastMCP to be installed.

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
