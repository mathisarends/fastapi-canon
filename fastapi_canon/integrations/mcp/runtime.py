from collections.abc import Sequence
from typing import Any

from dishka import AsyncContainer
from fastapi import APIRouter, FastAPI
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from starlette.routing import Match
from starlette.types import Lifespan, Scope

from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integrations.mcp import Mcp, McpMount, McpScope
from fastapi_canon.integrations.mcp.component_validation import ComponentValidation
from fastapi_canon.integrations.mcp.configuration import McpRuntime


def install_mcp(
    app: FastAPI,
    mounts: Sequence[McpMount],
    options: Mcp,
    container: AsyncContainer | None,
    configuration: McpRuntime,
) -> Lifespan[FastAPI]:
    server = FastMCP(
        options.name or app.title,
        on_duplicate="error",
        auth=configuration.auth,
        mask_error_details=options.mask_error_details,
    )
    if container is not None:
        server.add_middleware(_DishkaScope(container))
    server.add_middleware(ComponentValidation(server))
    for middleware in (*options.middleware, *configuration.middleware):
        server.add_middleware(middleware)
    for mount in mounts:
        server.mount(mount.server, namespace=mount.namespace)
    mcp_app = server.http_app(path="/", stateless_http=options.stateless_http)
    app.mount(options.path, mcp_app)
    return mcp_app.lifespan


def validate_mounts(mounts: Sequence[McpMount]) -> None:
    namespaces: set[str] = set()
    servers: set[int] = set()
    for mount in mounts:
        if mount.server.auth is not None:
            raise FeatureConfigurationError(
                "Configure MCP HTTP authentication on Mcp(auth=...), not a mounted feature server"
            )
        if mount.namespace in namespaces or id(mount.server) in servers:
            raise FeatureConfigurationError(
                "Duplicate MCP namespace or server contribution"
            )
        namespaces.add(mount.namespace)
        servers.add(id(mount.server))


def validate_routes(app: FastAPI, routers: tuple[APIRouter, ...], path: str) -> None:
    for router in (app.router, *routers):
        for route in router.routes:
            for endpoint in (path, path + "/"):
                for method in ("GET", "POST", "DELETE"):
                    scope: Scope = {
                        "type": "http",
                        "path": endpoint,
                        "root_path": "",
                        "method": method,
                    }
                    match, _ = route.matches(scope)
                    if match is Match.FULL or (
                        endpoint == path and match is Match.PARTIAL
                    ):
                        raise FeatureConfigurationError(
                            f"MCP path {path!r} conflicts with an existing route "
                            f"matching {method} {endpoint}"
                        )


class _DishkaScope(Middleware):
    def __init__(self, container: AsyncContainer) -> None:
        self._container = container

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        async with McpScope(self._container)():
            return await call_next(context)

    async def on_read_resource(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        async with McpScope(self._container)():
            return await call_next(context)

    async def on_get_prompt(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        async with McpScope(self._container)():
            return await call_next(context)
