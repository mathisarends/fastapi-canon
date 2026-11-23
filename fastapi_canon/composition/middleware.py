from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

from fastapi_canon.composition.lifespans import (
    ApplicationLifespan,
    callable_injection,
    read_signature,
)
from fastapi_canon.diagnostics import qualified_name
from fastapi_canon.errors import FeatureConfigurationError


def middleware_dependencies(declared: Middleware) -> tuple[tuple[str, Any], ...]:
    """Return the ``Inject[T]`` constructor parameters of one declared middleware."""
    owner = f"middleware {qualified_name(declared.cls)}"
    try:
        names = tuple(read_signature(declared.cls).parameters)
    except (TypeError, ValueError):
        return ()
    keywords = map(str, declared.kwargs)
    supplied = frozenset((*names[1 : 1 + len(declared.args)], *keywords))
    injection = callable_injection(declared.cls, owner=owner, supplied=supplied)
    conflicts = sorted(name for name, _ in injection.dependencies if name in supplied)
    if conflicts:
        msg = f"{owner} injects {', '.join(conflicts)}; do not also pass it explicitly"
        raise FeatureConfigurationError(msg)
    return injection.dependencies


def install_middleware(
    app: FastAPI, declared: tuple[Middleware, ...]
) -> tuple[ApplicationLifespan, ...]:
    """Add *declared* outside the current stack, first entry outermost.

    Return a lifespan that reports an injected middleware's startup failure, so the
    composed lifespan can unwind and close the container.
    """
    startup = _StartupFailure()
    bound = tuple(_bind(app, entry, startup) for entry in declared)
    app.user_middleware[:0] = bound
    injected = any(
        entry is not original for entry, original in zip(bound, declared, strict=True)
    )
    return (startup,) if injected else ()


def _bind(app: FastAPI, declared: Middleware, startup: _StartupFailure) -> Middleware:
    dependencies = middleware_dependencies(declared)
    if not dependencies:
        return declared
    return Middleware(
        _InjectedMiddleware,
        application=app,
        declared=declared,
        dependencies=dependencies,
        startup=startup,
    )


class _StartupFailure:
    """Hold an instantiation failure until the composed lifespan can raise it."""

    def __init__(self) -> None:
        self.error: Exception | None = None

    @asynccontextmanager
    async def __call__(self, _app: FastAPI) -> AsyncGenerator[None, None]:
        error, self.error = self.error, None
        if error is not None:
            raise error
        yield


class _InjectedMiddleware:
    """Create one middleware on its first ASGI call, when services can be awaited."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        application: FastAPI,
        declared: Middleware,
        dependencies: tuple[tuple[str, Any], ...],
        startup: _StartupFailure,
    ) -> None:
        self._app = app
        self._application = application
        self._declared = declared
        self._dependencies = dependencies
        self._startup = startup
        self._instance: ASGIApp | None = None
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            instance = await self._resolved()
        # Providers and constructors may fail arbitrarily; startup must still unwind.
        except Exception as error:
            if scope["type"] != "lifespan":
                raise
            self._startup.error = self._startup.error or error
            await self._app(scope, receive, send)
            return
        await instance(scope, receive, send)

    async def _resolved(self) -> ASGIApp:
        if self._instance is not None:
            return self._instance
        async with self._lock:
            if self._instance is None:
                container = self._application.state.dishka_container
                services = {
                    name: await container.get(dependency)
                    for name, dependency in self._dependencies
                }
                self._instance = self._declared.cls(
                    self._app,
                    *self._declared.args,
                    **self._declared.kwargs,
                    **services,
                )
            return self._instance
