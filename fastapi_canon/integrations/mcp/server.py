from collections.abc import Callable
from typing import Any, overload

from fastmcp import FastMCP

from fastapi_canon.integrations.mcp.dependencies import inject_handler


class McpServer(FastMCP):
    """FastMCP with registration-time support for Canon's Inject[T] marker."""

    def __init__(self, *args: Any, **options: Any) -> None:
        if options.get("tasks"):
            raise ValueError("Canon MCP does not yet support background tasks")
        super().__init__(*args, **options)

    def add_tool(self, tool: Any) -> Any:
        return super().add_tool(inject_handler(tool) if callable(tool) else tool)

    @overload
    def tool(self, name_or_fn: Callable[..., Any], **options: Any) -> Any: ...

    @overload
    def tool(
        self, name_or_fn: str | None = None, **options: Any
    ) -> Callable[[Callable[..., Any]], Any]: ...

    def tool(self, name_or_fn: Any = None, **options: Any) -> Any:
        if options.get("task"):
            raise ValueError("Canon MCP does not yet support background tasks")
        return self._register(super().tool, name_or_fn, options)

    def resource(self, uri: str, **options: Any) -> Callable[[Callable[..., Any]], Any]:
        if options.get("task"):
            raise ValueError("Canon MCP does not yet support background tasks")
        register = super().resource(uri, **options)
        return lambda function: register(inject_handler(function))

    def prompt(self, name_or_fn: Any = None, **options: Any) -> Any:
        if options.get("task"):
            raise ValueError("Canon MCP does not yet support background tasks")
        return self._register(super().prompt, name_or_fn, options)

    def _register(self, register: Any, name_or_fn: Any, options: dict[str, Any]) -> Any:
        if callable(name_or_fn):
            return register(inject_handler(name_or_fn), **options)
        decorator = register(name_or_fn, **options)
        return lambda function: decorator(inject_handler(function))
