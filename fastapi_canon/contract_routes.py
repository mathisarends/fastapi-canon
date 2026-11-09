"""Read the effective paths of registered routes across FastAPI versions."""

from collections.abc import Iterator, Sequence
from typing import Any

from starlette.routing import BaseRoute, Mount

RPC_ENDPOINT_ATTRIBUTE = "__canon_rpc_endpoint__"
WEBRTC_DECLARATION_ATTRIBUTE = "__canon_webrtc__"


def iter_contract_routes(
    routes: Sequence[BaseRoute], *, prefix: str = "", include_mounts: bool = True
) -> Iterator[tuple[str, Any]]:
    """Include router prefixes and recursively traverse mounted applications."""
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:  # pragma: no cover - FastAPI < 0.140
        contexts: Any = routes
    else:
        contexts = iter_route_contexts(routes)
    for route in contexts:
        original = getattr(route, "original_route", route)
        if isinstance(original, Mount):
            if include_mounts:
                yield from iter_contract_routes(
                    route.routes, prefix=prefix + route.path
                )
            continue
        path = getattr(route, "path_format", None) or getattr(route, "path", None)
        if path is not None:
            yield prefix + path, route
