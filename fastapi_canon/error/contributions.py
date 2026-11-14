from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from fastapi_canon.error.registry import ErrorRegistry

type RegistryContributions = tuple[ErrorRegistry, ...]


@runtime_checkable
class ErrorRegistrySource(Protocol):
    @property
    def error_registries(self) -> RegistryContributions: ...


def error_registries(source: object) -> RegistryContributions:
    """Read the same typed contribution contract from routers and endpoints."""
    if isinstance(source, ErrorRegistrySource):
        return source.error_registries
    return ()


def attach_error_registries(
    endpoint: object, registries: Sequence[ErrorRegistry]
) -> None:
    """Function metadata survives FastAPI router inclusion via the endpoint."""
    endpoint.__dict__["error_registries"] = tuple(dict.fromkeys(registries))
