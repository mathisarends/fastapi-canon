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

When auth or root middleware depends on application services, use
`McpOptions(auth_factory=..., middleware_factory=...)`. Each synchronous factory
receives the borrowed Dishka APP container and returns a native FastMCP
`AuthProvider` or a sequence of native FastMCP middleware instances. Supply
either `auth` or `auth_factory`. Factories require features with Dishka providers.

```python
from abc import ABC, abstractmethod

from dishka import AsyncContainer
from fastmcp.server.auth import AccessToken, TokenVerifier


class TokenAuthentication(ABC):
    @abstractmethod
    async def user_for_token(self, token: str) -> str | None: ...


class PortfolioVerifier(TokenVerifier):
    def __init__(self, container: AsyncContainer) -> None:
        super().__init__()
        self.container = container

    async def verify_token(self, token: str) -> AccessToken | None:
        authentication = await self.container.get(TokenAuthentication)
        user_id = await authentication.user_for_token(token)
        if user_id is None:
            return None
        return AccessToken(token=token, client_id=user_id, scopes=[])


options = McpOptions(auth_factory=PortfolioVerifier)
```

Register the application's `TokenAuthentication` implementation at APP scope.
Auth services resolved from the borrowed APP container must be APP-scoped. If verification
needs REQUEST resources, open and close an explicit short Dishka scope inside
`verify_token` and return detached identity data.

Factories run during preflight, before installation mutates the application.
They may be called again by `check()` or a separate application; keep construction
deterministic and free of dependency resolution, tasks or opened resources.
Adapters resolve services asynchronously at request time, without retaining a
FastAPI app or reading `app.state`. Canon retains ownership of the container and
closes it after MCP lifespan cleanup. Factory output failures report
`canon.mcp.E002`.

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
