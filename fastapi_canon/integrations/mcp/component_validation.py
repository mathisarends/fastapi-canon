from collections.abc import Sequence
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.utilities.components import FastMCPComponent

from fastapi_canon.error.types import FeatureConfigurationError


class ComponentValidation(Middleware):
    """Reject ambiguous public identities in the current caller's live catalog."""

    def __init__(self, server: FastMCP) -> None:
        self._server = server

    async def on_list_tools(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        result = await call_next(context)
        _validate_components(result)
        return result

    async def on_list_prompts(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        result = await call_next(context)
        _validate_components(result)
        return result

    async def on_list_resources(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        result = await call_next(context)
        _validate_components(result)
        return result

    async def on_list_resource_templates(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        result = await call_next(context)
        _validate_components(result)
        return result

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        _validate_components(await self._server.list_tools())
        return await call_next(context)

    async def on_get_prompt(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        _validate_components(await self._server.list_prompts())
        return await call_next(context)

    async def on_read_resource(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        _validate_components(await self._server.list_resources())
        _validate_components(await self._server.list_resource_templates())
        return await call_next(context)


def _validate_components(components: Sequence[FastMCPComponent]) -> None:
    seen: set[str] = set()
    for component in components:
        if component.key in seen:
            raise FeatureConfigurationError(
                f"Duplicate MCP component {component.key!r}; "
                "rename the local component or change its mount namespace"
            )
        seen.add(component.key)
