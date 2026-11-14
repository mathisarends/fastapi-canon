import asyncio
from dataclasses import dataclass

import httpx2
import pytest
from dishka import AsyncContainer, Provider, Scope
from fastapi import FastAPI
from fastmcp.server.auth import AccessToken, TokenVerifier, require_scopes
from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.asgi_transport import StreamingASGITransport
from pydantic import ConfigDict, Field

from fastapi_canon import Composition, Feature
from fastapi_canon.integrations.mcp import Mcp, McpMount, McpServer
from fastapi_canon.integrations.opentelemetry import Telemetry, instrument
from tests.fixtures.telemetry import SpanCapture
from tests.integration.integrations.mcp.test_runtime import connected


@dataclass(frozen=True)
class ToolCapability:
    turn: str
    tools: tuple[str, ...]


class ToolAccessRegistry:
    def __init__(self) -> None:
        self.capabilities: dict[str, ToolCapability] = {}
        self.executions: list[tuple[str, str]] = []

    async def issue(self, token: str, capability: ToolCapability) -> None:
        self.capabilities[token] = capability

    async def verify(self, token: str) -> ToolCapability | None:
        return self.capabilities.get(token)

    async def execute(self, token: str, turn: str, tool: str) -> str:
        capability = await self.verify(token)
        if (
            capability is None
            or capability.turn != turn
            or tool not in capability.tools
        ):
            raise PermissionError("capability does not authorize this turn and tool")
        self.executions.append((turn, tool))
        return turn + ":" + tool

    async def revoke(self, token: str) -> None:
        del self.capabilities[token]


class ToolCapabilityToken(AccessToken):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    registry: ToolAccessRegistry = Field(exclude=True)


class ToolCapabilityVerifier(TokenVerifier):
    def __init__(self, container: AsyncContainer) -> None:
        super().__init__()
        self.container = container

    async def verify_token(self, token: str) -> AccessToken | None:
        registry = await self.container.get(ToolAccessRegistry)
        capability = await registry.verify(token)
        if capability is None:
            return None
        return ToolCapabilityToken(
            token=token,
            client_id=capability.turn,
            scopes=list(capability.tools),
            registry=registry,
        )


@pytest.mark.parametrize("telemetry_enabled", [False, True], ids=["off", "on"])
async def test_typed_capability_authentication_over_instrumented_http(
    telemetry_enabled: bool,
) -> None:
    registry = ToolAccessRegistry()
    await registry.issue("turn-capability", ToolCapability("turn-1", ("read",)))
    dependencies = Provider()
    dependencies.provide(lambda: registry, provides=ToolAccessRegistry, scope=Scope.APP)
    server = McpServer("Capabilities")

    @server.tool(auth=require_scopes("read"))
    async def read(turn: str) -> str:
        token = get_access_token()
        assert isinstance(token, ToolCapabilityToken)
        assert "registry" not in token.model_dump()
        return await token.registry.execute(token.token, turn, "read")

    @server.tool(auth=require_scopes("write"))
    async def write() -> str:
        raise AssertionError("the capability must never execute this tool")

    feature = Feature(
        name="tools",
        providers=[dependencies],
        integrations=[
            McpMount(server=server, namespace="tools"),
            *instrument(ToolAccessRegistry),
        ],
    )
    with SpanCapture() as capture:
        app = Composition(
            features=[feature],
            integrations=[
                Telemetry(tracer_provider=capture.provider)
                if telemetry_enabled
                else Telemetry.disabled(),
                Mcp(auth_factory=ToolCapabilityVerifier),
            ],
        ).build(FastAPI())

        async with (
            asyncio.timeout(10),
            connected(app, auth="turn-capability") as client,
            httpx2.AsyncClient(
                transport=StreamingASGITransport(app), base_url="http://testserver"
            ) as http,
        ):
            assert [tool.name for tool in await client.list_tools()] == ["tools_read"]
            assert (
                await client.call_tool("tools_read", {"turn": "turn-1"})
            ).data == "turn-1:read"
            wrong_turn = await client.call_tool(
                "tools_read", {"turn": "turn-2"}, raise_on_error=False
            )
            assert wrong_turn.is_error
            denied = await client.call_tool("tools_write", raise_on_error=False)
            assert denied.is_error
            invalid = await http.post(
                "/mcp/",
                headers={"Authorization": "Bearer invalid"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            )
            assert invalid.status_code == 401
            await registry.revoke("turn-capability")
            revoked = await http.post(
                "/mcp/",
                headers={"Authorization": "Bearer turn-capability"},
                json={"jsonrpc": "2.0", "id": 2, "method": "initialize"},
            )
            assert revoked.status_code == 401

        assert registry.executions == [("turn-1", "read")]
        port_spans = [
            span
            for span in capture.spans
            if span.name.startswith("ToolAccessRegistry.")
        ]
        if telemetry_enabled:
            assert {span.name for span in port_spans} == {
                "ToolAccessRegistry.verify",
                "ToolAccessRegistry.execute",
            }
        else:
            assert port_spans == []
