from collections.abc import Sequence
from typing import Any

from dishka import AsyncContainer
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from starlette.types import Lifespan

from fastapi_canon.error.types import FeatureConfigurationError
from fastapi_canon.integrations.mcp import McpMount, McpOptions
from fastapi_canon.integrations.mcp.configuration import McpRuntime
from fastapi_canon.integrations.mcp.dependencies import request_scope


def install_mcp(
    app: FastAPI,
    mounts: Sequence[McpMount],
    options: McpOptions,
    container: AsyncContainer | None,
    configuration: McpRuntime,
) -> Lifespan[FastAPI]:
    server = FastMCP(
        options.name or app.title, on_duplicate="error", auth=configuration.auth
    )
    if container is not None:
        server.add_middleware(_DishkaScope(container))
    for middleware in configuration.middleware:
        server.add_middleware(middleware)
    for mount in mounts:
        server.mount(mount.server, namespace=mount.namespace)
    mcp_app = server.http_app(path="/")
    app.mount(options.path, mcp_app)
    return mcp_app.lifespan


def validate_mounts(mounts: Sequence[McpMount], options: McpOptions | None) -> None:
    if mounts and options is None:
        raise FeatureConfigurationError(
            "Features with MCP mounts require Composition(mcp=McpOptions())"
        )
    namespaces: set[str] = set()
    servers: set[int] = set()
    for mount in mounts:
        if mount.server.auth is not None:
            raise FeatureConfigurationError(
                "Configure MCP HTTP authentication on McpOptions(auth=...), not a mounted feature server"
            )
        if mount.namespace in namespaces or id(mount.server) in servers:
            raise FeatureConfigurationError(
                "Duplicate MCP namespace or server contribution"
            )
        namespaces.add(mount.namespace)
        servers.add(id(mount.server))


class _DishkaScope(Middleware):
    def __init__(self, container: AsyncContainer) -> None:
        self._container = container

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        async with request_scope(self._container):
            return await call_next(context)

    async def on_read_resource(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        async with request_scope(self._container):
            return await call_next(context)

    async def on_get_prompt(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        async with request_scope(self._container):
            return await call_next(context)
