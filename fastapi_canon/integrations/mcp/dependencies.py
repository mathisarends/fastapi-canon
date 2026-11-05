from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import Parameter, iscoroutinefunction, signature
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from dishka import AsyncContainer, Scope


class _Injection:
    __slots__ = ()


_MARKER = _Injection()
type Inject[T] = Annotated[T, _MARKER]
_ACTIVE: ContextVar[AsyncContainer] = ContextVar("canon_mcp_container")


def inject_handler(function: Callable[..., Any]) -> Callable[..., Any]:
    hints = get_type_hints(function, include_extras=True)
    sig = signature(function)
    dependencies: dict[str, Any] = {}
    for name, annotation in hints.items():
        if get_origin(annotation) is Inject or (
            get_origin(annotation) is Annotated and _MARKER in get_args(annotation)[1:]
        ):
            dependencies[name] = get_args(annotation)[0]
    if not dependencies:
        return function
    if not iscoroutinefunction(function):
        raise TypeError("MCP Inject[T] requires an async handler")
    if "return" in dependencies:
        raise TypeError("Inject[T] is only supported on parameters")

    @wraps(function)
    async def injected(**arguments: Any) -> Any:
        try:
            container = _ACTIVE.get()
        except LookupError as error:
            raise RuntimeError(
                "MCP injection requires an active Canon composition"
            ) from error
        for name, dependency in dependencies.items():
            arguments[name] = await container.get(dependency)
        return await function(**arguments)

    parameters = [
        parameter.replace(annotation=hints.get(name, parameter.annotation))
        for name, parameter in sig.parameters.items()
        if name not in dependencies
    ]
    if any(parameter.kind is Parameter.POSITIONAL_ONLY for parameter in parameters):
        raise TypeError("MCP handlers do not support positional-only parameters")
    injected.__signature__ = sig.replace(  # type: ignore[attr-defined]  # Public inspect signature override.
        parameters=parameters,
        return_annotation=hints.get("return", sig.return_annotation),
    )
    injected.__annotations__ = {
        name: value for name, value in hints.items() if name not in dependencies
    }
    return injected


@asynccontextmanager
async def request_scope(container: AsyncContainer) -> AsyncIterator[None]:
    async with container(scope=Scope.REQUEST) as scoped:
        token = _ACTIVE.set(scoped)
        try:
            yield
        finally:
            _ACTIVE.reset(token)
