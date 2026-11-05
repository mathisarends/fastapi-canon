"""Lazy optional exports for the package facade, outside runtime integrations."""

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "RpcChannel":
        from fastapi_canon.integrations.websockets import RpcChannel

        return RpcChannel
    from fastapi_canon.integrations.database import __getattr__ as database_export

    return database_export(name)
