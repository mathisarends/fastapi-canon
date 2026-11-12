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
    for route in _route_contexts(routes):
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


def iter_contract_mounts(
    routes: Sequence[BaseRoute],
) -> Iterator[tuple[str, Any]]:
    """Read mounted applications without entering their runtime."""
    for route in _route_contexts(routes):
        original = getattr(route, "original_route", route)
        if isinstance(original, Mount):
            yield route.path, original.app


def _route_contexts(routes: Sequence[BaseRoute]) -> Iterator[Any]:
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:  # pragma: no cover - FastAPI < 0.140
        yield from routes
    else:
        yield from iter_route_contexts(routes)
