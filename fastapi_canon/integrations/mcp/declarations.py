"""MCP integration declarations: ``Mcp`` for the composition, ``McpMount`` for features."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING, ClassVar

from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integration import (
    Installer,
    Integration,
    IntegrationContext,
    IntegrationError,
)

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from fastmcp import FastMCP
    from fastmcp.server.auth import AuthProvider
    from fastmcp.server.middleware import Middleware


@dataclass(frozen=True, slots=True, kw_only=True)
class Mcp(Integration):
    """Serve the MCP servers that features mount, at one application path.

    Enable it once with ``Composition(integrations=[Mcp(...)])``. Features add
    servers with ``Feature(integrations=[McpMount(...)])``.
    """

    name: str | None = None
    path: str = "/mcp"
    auth: AuthProvider | None = None
    stateless_http: bool | None = None
    mask_error_details: bool | None = None
    middleware: tuple[Middleware, ...] = ()
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
        for parameter in ("stateless_http", "mask_error_details"):
            value = getattr(self, parameter)
            if value is not None and not isinstance(value, bool):
                raise FeatureConfigurationError(
                    f"MCP {parameter} must be a bool or None"
                )
        object.__setattr__(self, "middleware", tuple(self.middleware))
        if self.middleware:
            from fastmcp.server.middleware import Middleware

            if any(not isinstance(item, Middleware) for item in self.middleware):
                raise FeatureConfigurationError(
                    "MCP middleware must contain Middleware instances"
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

    def prepare(self, context: IntegrationContext) -> Installer:
        """Check mounts, routes and runtime factories; return the MCP installer."""
        try:
            from fastapi_canon.integrations.mcp.configuration import prepare_runtime
            from fastapi_canon.integrations.mcp.runtime import (
                install_mcp,
                validate_mounts,
                validate_routes,
            )
        except ModuleNotFoundError as error:
            if error.name != "fastmcp":
                raise
            raise FeatureConfigurationError(
                "Install fastapi-canon[mcp] to enable MCP"
            ) from error
        mounts: tuple[McpMount, ...] = tuple(part for _, part in context.parts)
        validate_mounts(mounts)
        if context.app.middleware_stack is not None:
            raise FeatureConfigurationError("install MCP before the app starts")
        try:
            validate_routes(context.app, context.routers, self.path)
        except FeatureConfigurationError as error:
            raise IntegrationError(
                "canon.mcp.E001",
                str(error),
                "Move the conflicting HTTP route or choose a different MCP path.",
            ) from error
        try:
            runtime = prepare_runtime(self, context.container)
        except FeatureConfigurationError as error:
            raise IntegrationError(
                "canon.mcp.E002",
                str(error),
                "Use synchronous factories that borrow the APP container without "
                "starting resources.",
            ) from error
        return Installer(
            lambda app, container: install_mcp(app, mounts, self, container, runtime)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class McpMount:
    """A feature's FastMCP server, mounted under *namespace* by ``Mcp``."""

    integration: ClassVar[type[Integration]] = Mcp
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
