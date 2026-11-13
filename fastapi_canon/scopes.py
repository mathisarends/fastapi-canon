"""Short service scopes and HTTP resource lifetimes owned by composition."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from inspect import getattr_static, iscoroutinefunction

from dishka import AsyncContainer, Scope
from dishka.integrations.starlette import ContainerMiddleware
from fastapi import APIRouter, Depends, FastAPI, Request
from starlette.requests import HTTPConnection
from starlette.types import Receive, Send
from starlette.types import Scope as ASGIScope


class ServiceScope[ServiceT]:
    """Resolve a service in a fresh scope, independently of an HTTP request.

    Register this factory at APP scope. Each call borrows the application's
    container and owns a new REQUEST scope; it never closes the parent.
    """

    def __init__(
        self,
        container: AsyncContainer,
        service: type[ServiceT],
        *,
        component: str = "",
    ) -> None:
        self._container = container
        self._service = service
        self._component = component

    @asynccontextmanager
    async def __call__(self) -> AsyncGenerator[ServiceT]:
        async with self._container(scope=Scope.REQUEST) as scoped:
            yield await scoped.get(self._service, component=self._component)


class Scoped[UseCaseT]:
    """Execute one async callable use case in a fresh ServiceScope per call.

    Register at APP scope with the application container. The result is returned
    only after scope cleanup succeeds. Database transaction behavior belongs to
    the configured providers. Return materialized values, not scoped resources.
    """

    def __init__(
        self,
        container: AsyncContainer,
        use_case: type[UseCaseT],
        *,
        component: str = "",
    ) -> None:
        if container.scope is not Scope.APP:
            raise ValueError("Scoped requires the application APP container")
        if not iscoroutinefunction(getattr_static(use_case, "__call__", None)):
            raise TypeError("Scoped requires a use case with async __call__")
        self._scope = ServiceScope(container, use_case, component=component)

    async def __call__[**Parameters, ResultT](
        self: "Scoped[Callable[Parameters, Awaitable[ResultT]]]",
        *args: Parameters.args,
        **kwargs: Parameters.kwargs,
    ) -> ResultT:
        async with self._scope() as use_case:
            return await use_case(*args, **kwargs)


class _SocketScopeMiddleware(ContainerMiddleware):
    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            await self.app(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)


def install_scopes(app: FastAPI, container: AsyncContainer) -> None:
    app.state.dishka_container = container
    app.add_middleware(_SocketScopeMiddleware)
    dependency = Depends(_http_request_scope, scope="function")
    # Re-include already declared routes through FastAPI's public cloning API.
    # Their existing app dependencies are already part of the route; temporarily
    # omit those at the parent to avoid executing use_cache=False twice.
    existing = APIRouter()
    existing.routes.extend(app.router.routes)
    dependencies = app.router.dependencies
    app.router.dependencies = [dependency]
    app.router.routes.clear()
    app.include_router(existing)
    app.router.dependencies = [dependency, *dependencies]


async def _http_request_scope(
    connection: HTTPConnection,
) -> AsyncGenerator[None]:
    if not isinstance(connection, Request):
        yield
        return
    async with connection.app.state.dishka_container(
        {Request: connection}, scope=Scope.REQUEST
    ) as container:
        connection.state.dishka_container = container
        try:
            yield
        finally:
            # Async generator injection runs on first iteration, after this
            # scope exits. Keep APP dependencies and ServiceScope factories
            # resolvable without reopening or retaining REQUEST resources.
            connection.state.dishka_container = connection.app.state.dishka_container
