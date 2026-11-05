from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from inspect import Parameter, signature
from typing import TYPE_CHECKING, Any, get_type_hints

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from fastapi import WebSocket


from fastapi_canon.integrations.websockets.dependencies import RpcResolver


class DishkaResolver:
    """Adapt a Dishka container to connection and call-scoped RPC resolution."""

    def __init__(self, container: AsyncContainer) -> None:
        self._container = container

    async def resolve[DependencyT](self, dependency: type[DependencyT]) -> DependencyT:
        return await self._container.get(dependency)

    @asynccontextmanager
    async def enter_connection(
        self,
        context: Mapping[type[Any], object],
    ) -> AsyncGenerator[RpcResolver, None]:
        try:
            from dishka import Scope
        except ImportError as error:
            raise ModuleNotFoundError(
                "The Dishka integration requires a complete fastapi-canon installation"
            ) from error
        container_scope = getattr(self._container, "scope", None)
        if container_scope is not None and container_scope is not Scope.APP:
            raise ValueError(
                "DishkaResolver needs the APP container "
                "(app.state.dishka_container), got a "
                f"{container_scope.name} container. Canon opens the SESSION "
                "scope per connection itself."
            )
        async with self._container(
            context=dict(context),
            scope=Scope.SESSION,
        ) as session_container:
            yield DishkaResolver(session_container)

    @asynccontextmanager
    async def enter_scope(self) -> AsyncGenerator[RpcResolver, None]:
        async with self._container() as request_container:
            yield DishkaResolver(request_container)


class Dishka:
    """Resolve ``CanonWebSocketRouter`` dependencies with the app's root container.

    Context functions and hooks resolve in a short REQUEST scope before the
    connection is accepted. Providers own resources and their cleanup.
    Existing ``FromDishka[T]`` context parameters remain supported.
    """

    def for_websocket(self, websocket: WebSocket) -> DishkaResolver:
        return DishkaResolver(websocket.app.state.dishka_container)

    @asynccontextmanager
    async def preparation_scope(
        self, websocket: WebSocket
    ) -> AsyncGenerator[RpcResolver]:
        from dishka import Scope
        from fastapi import WebSocket

        previous = getattr(websocket.state, "dishka_container", None)
        parent = previous or websocket.app.state.dishka_container
        async with parent(
            {WebSocket: websocket}, scope=Scope.REQUEST
        ) as request_container:
            websocket.state.dishka_container = request_container
            try:
                yield DishkaResolver(request_container)
            finally:
                if previous is None:
                    del websocket.state.dishka_container
                else:
                    websocket.state.dishka_container = previous

    def dependency[FunctionT: Callable[..., Any]](
        self, function: FunctionT
    ) -> FunctionT:
        try:
            from dishka.integrations.base import wrap_injection
            from fastapi import WebSocket
        except ImportError as error:
            raise ModuleNotFoundError(
                "Reinstall fastapi-canon with its required Dishka dependency"
            ) from error
        hints = get_type_hints(function)
        parameters = signature(function).parameters
        websocket_name = next(
            (name for name in parameters if hints.get(name) is WebSocket), None
        )
        additional_params = []
        if websocket_name is None:
            websocket_name = "_canon_websockets_websocket"
            while websocket_name in parameters:
                websocket_name = "_" + websocket_name
            additional_params.append(
                Parameter(
                    websocket_name,
                    Parameter.KEYWORD_ONLY,
                    annotation=WebSocket,
                )
            )
        # Dishka's FastAPI injector used Request for missing transport arguments
        # in 1.7; a WebSocket dependency needs the WebSocket's container instead.
        return wrap_injection(
            func=function,
            is_async=True,
            additional_params=additional_params,
            container_getter=lambda _, values: (
                values[websocket_name].state.dishka_container
            ),
        )
