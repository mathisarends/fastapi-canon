from collections.abc import Callable
from typing import Protocol

from dishka import Provider
from fastapi import APIRouter
from starlette.middleware import Middleware

from fastapi_canon.composition.feature import ErrorOptions, Feature
from fastapi_canon.integration import Integration


class CompositionSource(Protocol):
    """Read-only configuration shared by preflight and installation."""

    @property
    def features(self) -> tuple[Feature, ...]: ...

    @property
    def errors(self) -> ErrorOptions: ...

    @property
    def router_factory(self) -> Callable[[], APIRouter] | None: ...

    @property
    def integrations(self) -> tuple[Integration, ...]: ...

    @property
    def request_logging(self) -> bool: ...

    @property
    def overrides(self) -> tuple[Provider | Callable[[], Provider], ...]: ...

    @property
    def middleware(self) -> tuple[Middleware, ...]: ...

    @property
    def outer_middleware(self) -> tuple[Middleware, ...]: ...
