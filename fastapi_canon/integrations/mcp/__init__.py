"""Optional FastMCP composition and Dishka injection."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .declarations import McpMount, McpOptions
from .dependencies import Inject

if TYPE_CHECKING:
    from .server import McpServer

__all__ = ["Inject", "McpMount", "McpOptions", "McpServer"]


def __getattr__(name: str) -> Any:
    if name == "McpServer":
        return import_module(f"{__name__}.server").McpServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
