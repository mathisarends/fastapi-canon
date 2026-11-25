# 12. MCP

Expose application services as tools for AI agents, with the same Dishka
services your routes use. Install `fastapi-canon[mcp]`.

## A tool

```python
from dishka import Provider, Scope, provide
from fastapi import FastAPI

from fastapi_canon import Composition, Feature
from fastapi_canon.integrations.mcp import Inject, McpMount, Mcp, McpServer


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
    integrations=[McpMount(server=portfolio_mcp, namespace="portfolio")],
)

app = Composition(
    features=[portfolio_feature],
    integrations=[Mcp()],
).build(FastAPI(title="Maximus"))
```

Agents see one endpoint at `/mcp` with the tool `portfolio_get_portfolio`;
injected parameters are hidden from the input schema. Each tool call gets its
own request scope. Mounts never open an endpoint on their own: `Mcp()` must be
enabled on the composition, which is also where authentication lives.

## Authentication

FastAPI dependencies and router policies do not protect MCP routes. Pass a
FastMCP auth provider with `Mcp(auth=...)`, or build one from your services:

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


options = Mcp(auth_factory=PortfolioVerifier)
```

## Errors

Tools use FastMCP's error handling, not Problem Details. Raise
`fastmcp.exceptions.ToolError` for messages meant for the agent, and create
servers with `McpServer("Name", mask_error_details=True)` so internal exception
text stays private.

## Good to know

- `Mcp(name="Agent API", path="/agent")` changes the server name and path.
- `Mcp(stateless_http=True)` for deployments without session affinity.
- Work that outlives a request enters its own scope with `McpScope`.
- A plain `FastMCP` server can be mounted too; it uses FastMCP's own dependencies.

---

Previous: [11. WebRTC](11-webrtc.md) · [Learning path](12-mcp.md)
