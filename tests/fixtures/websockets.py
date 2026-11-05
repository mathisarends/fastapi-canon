from typing import Any

from fastapi_canon.integrations.websockets import CanonWebSocketRouter
from fastapi_canon.integrations.websockets.definition import RpcDefinition


def router_for(definition: RpcDefinition, **options: Any) -> CanonWebSocketRouter:
    router = CanonWebSocketRouter(**options)
    definition.freeze()
    for endpoint in definition.endpoints:
        router.mount(endpoint)
    return router
