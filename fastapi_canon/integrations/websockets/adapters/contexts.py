import inspect
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, field
from typing import Annotated, Any, get_type_hints

from fastapi import Depends, WebSocket
from starlette.concurrency import run_in_threadpool

from fastapi_canon.integrations.websockets.dependencies import (
    RpcResolver,
    injected_parameter,
)


class ContextDependencies:
    """Compile typed context hooks into ordinary FastAPI dependencies."""

    def __init__(
        self,
        context: Callable[..., Any],
        hooks: Sequence[Callable[..., Any]],
        scope: Callable[[WebSocket], AbstractAsyncContextManager[RpcResolver]],
        adapt: Callable[[Callable[..., Any]], Callable[..., Any]],
    ) -> None:
        self._hooks: dict[type[Any], Callable[..., Any]] = {}
        self._compiled: dict[Callable[..., Any], Callable[..., Any]] = {}
        self._visiting: set[Callable[..., Any]] = set()
        self._getters: dict[type[Any], Callable[..., Any]] = {}
        self._provided: dict[Callable[..., Any], tuple[type[Any], ...]] = {}
        self._adapt = adapt

        async def preparation(websocket: WebSocket) -> AsyncGenerator[_Preparation]:
            async with AsyncExitStack() as stack:
                resolver = await stack.enter_async_context(scope(websocket))
                yield _Preparation(resolver, stack)

        self._preparation = preparation
        for hook in hooks:
            dependency = _result_type(hook)
            if dependency in self._hooks:
                raise TypeError(f"Multiple context hooks provide {dependency.__name__}")
            self._hooks[dependency] = hook
        for hook in hooks:
            self._compile(hook)
        self.dependency = self._compile(context, root=True)
        self._provided_types = self._provided[context]

    @property
    def provided_types(self) -> tuple[type[Any], ...]:
        return self._provided_types

    def _compile(
        self, function: Callable[..., Any], *, root: bool = False
    ) -> Callable[..., Any]:
        if function in self._visiting:
            raise TypeError(f"Cyclic context hooks involving {function.__qualname__}")
        if not root and function in self._compiled:
            return self._compiled[function]
        result_type = _result_type(function)
        provided = [result_type]
        self._visiting.add(function)
        adapted = self._adapt(function)
        signature = inspect.signature(adapted)
        hints = get_type_hints(adapted, include_extras=True)
        parameters = []
        for parameter in signature.parameters.values():
            if parameter.kind not in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                raise TypeError(
                    f"Context hook parameter {parameter.name!r} must be "
                    "positional-or-keyword or keyword-only"
                )
            annotation = hints.get(parameter.name, parameter.annotation)
            injected = injected_parameter(parameter.name, annotation)
            if injected is not None:
                hook = self._hooks.get(injected.dependency)
                dependency = (
                    self._compile(hook)
                    if hook is not None
                    else self._getter(injected.dependency)
                )
                if hook is not None:
                    provided.extend(self._provided[hook])
                annotation = Annotated[Any, Depends(dependency)]
                parameter = parameter.replace(default=inspect.Parameter.empty)
            parameters.append(parameter.replace(annotation=annotation))
        scope_name = "_canon_preparation"
        while scope_name in signature.parameters:
            scope_name += "_"
        parameters.append(
            inspect.Parameter(
                scope_name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Annotated[_Preparation, Depends(self._preparation)],
            )
        )

        async def invoke(**arguments: Any) -> Any:
            preparation: _Preparation = arguments.pop(scope_name)
            result = (
                await adapted(**arguments)
                if inspect.iscoroutinefunction(adapted)
                else await run_in_threadpool(adapted, **arguments)
            )
            preparation.values[result_type] = result
            if root:
                # FastAPI's generator dependency also exits on errors before the
                # root runs. Successful admission closes early, before accept().
                await preparation.stack.aclose()
                return dict(preparation.values)
            return result

        invoke.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]  # Public FastAPI signature adapter.
        invoke.__annotations__ = {
            parameter.name: parameter.annotation for parameter in parameters
        }
        invoke.__annotations__["return"] = dict if root else result_type
        self._visiting.remove(function)
        self._provided[function] = tuple(dict.fromkeys(provided))
        if not root:
            self._compiled[function] = invoke
        return invoke

    def _getter(self, dependency: type[Any]) -> Callable[..., Any]:
        if dependency not in self._getters:

            async def resolve(
                preparation: Annotated[_Preparation, Depends(self._preparation)],
            ) -> Any:
                return await preparation.resolver.resolve(dependency)

            self._getters[dependency] = resolve
        return self._getters[dependency]


@dataclass
class _Preparation:
    resolver: RpcResolver
    stack: AsyncExitStack
    values: dict[type[Any], object] = field(default_factory=dict)


def _result_type(function: Callable[..., Any]) -> type[Any]:
    if (
        not callable(function)
        or inspect.isclass(function)
        or inspect.isasyncgenfunction(function)
        or inspect.isgeneratorfunction(function)
    ):
        raise TypeError("Context hooks must be functions returning a concrete value")
    result = get_type_hints(function).get("return")
    if result in (Any, object, type(None)) or not inspect.isclass(result):
        raise TypeError(
            f"Context hook {function.__qualname__} needs a concrete class "
            "as its return annotation"
        )
    return result
