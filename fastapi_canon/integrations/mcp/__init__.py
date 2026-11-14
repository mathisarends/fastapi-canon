"""Optional FastMCP composition and Dishka injection.

Enable ``Mcp`` in ``Composition(integrations=...)`` and mount feature servers with
``McpMount`` in ``Feature(integrations=...)``.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .declarations import Mcp, McpMount
from .dependencies import Inject, McpScope

if TYPE_CHECKING:
    from .server import McpServer

__all__ = ["Inject", "Mcp", "McpMount", "McpScope", "McpServer"]


def __getattr__(name: str) -> Any:
    if name == "McpServer":
        return import_module(f"{__name__}.server").McpServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
