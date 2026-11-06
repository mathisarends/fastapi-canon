from collections.abc import Callable
from dataclasses import dataclass
from inspect import iscoroutine
from typing import Any

from dishka import AsyncContainer
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import Middleware

from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integrations.mcp import McpOptions


@dataclass(frozen=True, slots=True)
class McpRuntime:
    auth: AuthProvider | None
    middleware: tuple[Middleware, ...]


def prepare_runtime(
    options: McpOptions, container: AsyncContainer | None
) -> McpRuntime:
    auth = options.auth
    middleware: tuple[Middleware, ...] = ()
    if options.auth_factory is not None:
        auth = _build(options.auth_factory, container, "auth_factory")
        if not isinstance(auth, AuthProvider):
            raise FeatureConfigurationError(
                "MCP auth_factory must return an AuthProvider"
            )
    if options.middleware_factory is not None:
        result = _build(options.middleware_factory, container, "middleware_factory")
        try:
            middleware = tuple(result)
        except TypeError as error:
            raise FeatureConfigurationError(
                "MCP middleware_factory must return a sequence of Middleware instances"
            ) from error
        if any(not isinstance(item, Middleware) for item in middleware):
            raise FeatureConfigurationError(
                "MCP middleware_factory must return a sequence of Middleware instances"
            )
    return McpRuntime(auth, middleware)


def _build(
    factory: Callable[[AsyncContainer], Any],
    container: AsyncContainer | None,
    parameter: str,
) -> Any:
    if container is None:
        raise FeatureConfigurationError(
            f"MCP {parameter} requires features with Dishka providers"
        )
    try:
        result = factory(container)
    except Exception as error:
        raise FeatureConfigurationError(f"MCP {parameter} failed") from error
    if iscoroutine(result):
        result.close()
        raise FeatureConfigurationError(f"MCP {parameter} must return synchronously")
    return result
