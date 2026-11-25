from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from fastapi_canon.integrations.websockets.adapters.fastapi import CanonWebSocketRouter
from fastapi_canon.integrations.websockets.channel import RpcChannel, declare_socket
from fastapi_canon.integrations.websockets.definition import RpcDefinition


def router_for(definition: RpcDefinition, **options: Any) -> CanonWebSocketRouter:
    router = CanonWebSocketRouter(**options)
    definition.freeze()
    for endpoint in definition.endpoints:
        router.mount(endpoint)
    return router


class CamelModel(BaseModel):
    """Application-defined camelCase wire names, as consumers configure them."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


def socket_of[Handler: Callable[..., Any]](
    channel: RpcChannel, name: str | None = None, /, **options: Any
) -> Callable[[Handler], Handler]:
    """Declare a socket handler for mounting with the low-level adapter."""

    def decorate(handler: Handler) -> Handler:
        declare_socket(channel, handler, name=name, **options)
        return handler

    return decorate
