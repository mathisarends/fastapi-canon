from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING

from fastapi_canon.error.types import FeatureConfigurationError

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from fastmcp import FastMCP
    from fastmcp.server.auth import AuthProvider
    from fastmcp.server.middleware import Middleware


@dataclass(frozen=True, slots=True, kw_only=True)
class McpMount:
    server: FastMCP
    namespace: str

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", self.namespace
        ):
            raise FeatureConfigurationError(
                "MCP namespace must contain only letters, digits, underscores or hyphens"
            )
        try:
            from fastmcp import FastMCP
        except ModuleNotFoundError as error:
            if error.name != "fastmcp":
                raise
            raise FeatureConfigurationError(
                "Install fastapi-canon[mcp] to mount MCP servers"
            ) from error
        if not isinstance(self.server, FastMCP):
            raise FeatureConfigurationError("MCP server must be a FastMCP instance")


@dataclass(frozen=True, slots=True, kw_only=True)
class McpOptions:
    name: str | None = None
    path: str = "/mcp"
    auth: AuthProvider | None = None
    auth_factory: Callable[[AsyncContainer], AuthProvider] | None = None
    middleware_factory: Callable[[AsyncContainer], Sequence[Middleware]] | None = None

    def __post_init__(self) -> None:
        if self.auth is not None and self.auth_factory is not None:
            raise FeatureConfigurationError("Choose MCP auth or auth_factory, not both")
        for parameter in ("auth_factory", "middleware_factory"):
            factory = getattr(self, parameter)
            if factory is not None and (
                not callable(factory)
                or iscoroutinefunction(factory)
                or iscoroutinefunction(type(factory).__call__)
            ):
                raise FeatureConfigurationError(
                    f"MCP {parameter} must be a synchronous callable or None"
                )
        if self.name is not None and (
            not isinstance(self.name, str) or not self.name.strip()
        ):
            raise FeatureConfigurationError(
                "MCP name must be a non-empty string or None"
            )
        if (
            not isinstance(self.path, str)
            or not self.path.startswith("/")
            or self.path == "/"
            or any(c.isspace() or c in "?#\\" for c in self.path)
            or "//" in self.path
            or any(part in {".", ".."} for part in self.path.split("/"))
        ):
            raise FeatureConfigurationError(
                "MCP path must be an absolute non-root URL path"
            )
        object.__setattr__(self, "path", self.path.rstrip("/"))
