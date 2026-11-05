"""One application router with explicit, lazily loaded transport adapters."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter as FastAPIRouter
from fastapi.params import Depends
from fastapi.routing import APIWebSocketRoute
from pydantic import BaseModel
from starlette.routing import WebSocketRoute

from fastapi_canon.contract_routes import iter_contract_routes
from fastapi_canon.exceptions import ConfigurationError
from fastapi_canon.router import CanonRouter

if TYPE_CHECKING:
    from fastapi_canon.integrations.webrtc import Engine, EventHook, IceServerSource
    from fastapi_canon.integrations.websockets import RpcChannel


class APIRouter(CanonRouter):
    """Compose HTTP, SSE, JSON-RPC channels and WebRTC sessions by feature."""

    def add_api_websocket_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        name: str | None = None,
        *,
        dependencies: Sequence[Depends] | None = None,
    ) -> None:
        key = _websocket_path(self.prefix + path)
        if any(
            key == _websocket_path(existing)
            for existing, route in iter_contract_routes(self.routes)
            if isinstance(getattr(route, "original_route", route), WebSocketRoute)
        ):
            raise ConfigurationError(f"Duplicate WebSocket path: {self.prefix + path}")
        super().add_api_websocket_route(
            path, endpoint, name=name, dependencies=dependencies
        )

    def include_router(self, router: FastAPIRouter, **options: Any) -> None:
        """Include routes, rejecting ambiguous WebSocket paths before mutation."""
        existing = {
            _websocket_path(path)
            for path, route in iter_contract_routes(self.routes)
            if isinstance(getattr(route, "original_route", route), WebSocketRoute)
        }
        incoming: set[str] = set()
        prefix = self.prefix + options.get("prefix", "")
        for path, route in iter_contract_routes(router.routes):
            if not isinstance(getattr(route, "original_route", route), WebSocketRoute):
                continue
            key = _websocket_path(prefix + path)
            if key in existing or key in incoming:
                raise ConfigurationError(f"Duplicate WebSocket path: {prefix + path}")
            incoming.add(key)
        super().include_router(router, **options)

    def include_channel(
        self,
        channel: RpcChannel,
        *,
        path: str,
        namespace: str | None = None,
        name: str | None = None,
        dependencies: Sequence[Depends] = (),
        path_model: type[BaseModel] | None = None,
        subprotocol: str | None = None,
        summary: str | None = None,
        **options: Any,
    ) -> None:
        """Mount a channel snapshot at one JSON-RPC WebSocket endpoint.

        ``namespace`` optionally replaces the channel's root namespace.
        Endpoint names default to the complete local path, including the prefix.
        Resolver, context, limits and middleware options configure this connection.
        The source channel stays reusable and mutable after registration.
        """
        from fastapi_canon.integrations.websockets import (
            CanonWebSocketRouter,
            RpcChannel,
        )

        if not isinstance(channel, RpcChannel):
            raise ConfigurationError("include_channel expects an RpcChannel")
        snapshot = RpcChannel()
        snapshot.include_channel(
            channel,
            namespace=channel.namespace if namespace is None else namespace,
        )
        protocol = snapshot.freeze()
        if protocol.streams or protocol.sockets:
            raise ConfigurationError(
                "include_channel accepts JSON-RPC methods, events and message streams; "
                "binary streams and raw sockets require their own WebSocket endpoints"
            )
        adapter = CanonWebSocketRouter(prefix=self.prefix, **options)
        adapter.socket(
            path,
            channels=[snapshot],
            name=name if name is not None else _channel_name(self.prefix + path),
            path_model=path_model,
            subprotocol=subprotocol,
            summary=summary,
        )
        adapter.freeze()
        # Keep the full prefix in RPC metadata while registering the native route
        # once on this router. Including the prefixed adapter would double it.
        route = adapter.routes[0]
        if not isinstance(route, APIWebSocketRoute):
            raise ConfigurationError("The channel adapter did not produce a WebSocket")
        self.add_api_websocket_route(
            path,
            route.endpoint,
            name=route.name,
            dependencies=[*dependencies, *route.dependencies],
        )
        self._included_error_registries.extend(
            getattr(adapter, "_canon_error_registries", ())
        )

    def sse[Endpoint: Callable[..., Any]](
        self, path: str, **options: Any
    ) -> Callable[[Endpoint], Endpoint]:
        """Declare typed SSE with serialization and terminal completion/problems."""
        from fastapi_canon.integrations.sse import CanonStreamRouter

        adapter = CanonStreamRouter(
            namespace=self.namespace,
            generate_unique_id_function=self.generate_unique_id_function,
            error_registry=self.error_registry,
            raises=self.raises,
            http_statuses=self.http_statuses,
        )
        register = adapter.stream(path, **options)

        def decorate(endpoint: Endpoint) -> Endpoint:
            register(endpoint)
            self.include_router(adapter)
            return endpoint

        return decorate

    def webrtc[Factory: Callable[..., Any]](
        self,
        path: str,
        *,
        engine: Engine | None = None,
        ice_servers: IceServerSource | None = None,
        on_event: EventHook | None = None,
        allowed_origins: Sequence[str] | None = None,
        **options: Any,
    ) -> Callable[[Factory], Factory]:
        """Declare a WebRTC session factory with managed signaling and cleanup.

        Session options include media, data_channels, limits, max_sessions,
        dependencies and raises. A factory may also supply its session's engine.
        """
        from fastapi_canon.integrations.webrtc import CanonWebRtcRouter

        adapter = CanonWebRtcRouter(
            error_registry=self.error_registry,
            raises=self.raises,
            engine=engine,
            ice_servers=ice_servers,
            on_event=on_event,
            allowed_origins=allowed_origins,
        )
        register = adapter.session(path, **options)

        def decorate(factory: Factory) -> Factory:
            register(factory)
            self.include_router(adapter)
            return factory

        return decorate


def _websocket_path(path: str) -> str:
    return re.sub(r"\{[^}:]+(?=[:}])", "{parameter", path)


def _channel_name(path: str) -> str:
    value = re.sub(r"[{}]", "", path)
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return f"rpc_{value}" if value else "rpc_root"
