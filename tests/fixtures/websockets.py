from typing import Any

from fastapi_canon.integrations.websockets import RpcRouter
from fastapi_canon.integrations.websockets.definition import RpcDefinition


def router_for(definition: RpcDefinition, **options: Any) -> RpcRouter:
    router = RpcRouter(**options)
    definition.freeze()
    for endpoint in definition.endpoints:
        router.mount(endpoint)
    return router
