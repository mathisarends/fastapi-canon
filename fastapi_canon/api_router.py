"""One application router with explicit, lazily loaded transport adapters."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict, Unpack

from fastapi import APIRouter as FastAPIRouter
from fastapi.params import Depends
from fastapi.routing import APIWebSocketRoute
from pydantic import BaseModel
from starlette.routing import BaseRoute, WebSocketRoute

from fastapi_canon.contract_routes import iter_contract_routes
from fastapi_canon.error.contributions import error_registries
from fastapi_canon.exceptions import ConfigurationError
from fastapi_canon.injected_route import InjectedRoute, inject_endpoint
from fastapi_canon.router import APIRouteOptions as APIRouteOptions
from fastapi_canon.router import CanonRouter
from fastapi_canon.router import IncludeRouterOptions as IncludeRouterOptions
from fastapi_canon.router import RouteOptions as RouteOptions
from fastapi_canon.router import RouterOptions as RouterOptions

if TYPE_CHECKING:
    from fastapi_canon.error import AnyError
    from fastapi_canon.integrations.webrtc import (
        DataChannelSpec,
        EventHook,
        IceServerSource,
        MediaPolicy,
        Role,
        SessionCapacity,
        SessionLimits,
    )
    from fastapi_canon.integrations.webrtc.engines import Engine
    from fastapi_canon.integrations.websockets import (
        RpcBeforeAccept,
        RpcChannel,
        RpcDrain,
        RpcErrorBinding,
        RpcErrorMapper,
        RpcLimits,
        RpcMessageType,
        RpcMiddlewareLike,
        RpcRejections,
        RpcResolverLike,
        RpcSocketProtocol,
    )
    from fastapi_canon.integrations.websockets.adapters.fastapi import (
        FastApiResolver,
    )


class StreamOptions(TypedDict, total=False):
    """Operation options accepted by ``APIRouter.sse()``.

    Streams infer their response model and always use ``EventSourceResponse``,
    so the response options of ``APIRouter.get()`` are not available here.
    """

    methods: list[str] | None
    operation_id: str | None
    summary: str | None
    description: str | None
    tags: list[str | Enum] | None
    dependencies: Sequence[Depends] | None
    raises: Sequence[AnyError]
    http_statuses: Sequence[int]
    status_code: int | None
    responses: dict[int | str, dict[str, Any]] | None
    response_description: str
    deprecated: bool | None
    include_in_schema: bool
    name: str | None
    openapi_extra: dict[str, Any] | None
    callbacks: list[BaseRoute] | None


class ChannelOptions(TypedDict, total=False):
    """Connection options for ``channel``, ``include_channel`` and socket decorators."""

    version: int
    context: Callable[..., Awaitable[Any]] | None
    resolver: RpcResolverLike | FastApiResolver | None
    hooks: Sequence[Callable[..., Any]]
    rejects: Sequence[RpcErrorBinding[Any]]
    rejections: RpcRejections | None
    middleware: Sequence[RpcMiddlewareLike]
    error_mapper: RpcErrorMapper | None
    limits: RpcLimits | None
    strict_errors: bool
    drain: RpcDrain | None
    before_accept: RpcBeforeAccept | None


class SessionOptions(TypedDict, total=False):
    """Session options accepted by ``APIRouter.webrtc()``."""

    name: str | None
    role: Role
    media: MediaPolicy | None
    data_channels: Sequence[DataChannelSpec]
    limits: SessionLimits | None
    max_sessions: int | None
    capacity: SessionCapacity | None
    dependencies: Sequence[Depends] | None
    raises: Sequence[AnyError]


class APIRouter(CanonRouter):
    """Compose HTTP, SSE, WebSocket channels, streams, sockets and WebRTC by feature.

    HTTP and SSE endpoints resolve ``Inject[T]`` and ``FromDishka[T]`` parameters
    without ``route_class=DishkaRoute`` or ``@inject``. An explicit
    ``route_class`` replaces this behavior.
    """

    def __init__(self, **kwargs: Unpack[RouterOptions]) -> None:
        kwargs.setdefault("route_class", InjectedRoute)
        self._pending_channels: list[tuple[RpcChannel, dict[str, Any]]] = []
        super().__init__(**kwargs)

    @property
    def routes(self) -> list[BaseRoute]:
        """Routes, including channels declared with ``channel()`` (mounted now)."""
        pending = getattr(self, "_pending_channels", None)
        if pending:
            self._pending_channels = []
            for channel, settings in pending:
                self.include_channel(channel, **settings)
                # Later registrations would never reach the mounted snapshot.
                channel.freeze()
        return self._routes

    @routes.setter
    def routes(self, routes: list[BaseRoute]) -> None:
        self._routes = routes

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

    def include_router(
        self, router: FastAPIRouter, **options: Unpack[IncludeRouterOptions]
    ) -> None:
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
        **options: Unpack[ChannelOptions],
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
                "declare binary streams with @router.binary_stream and sockets with @router.socket"
            )
        adapter = CanonWebSocketRouter(prefix=self.prefix, **options)
        adapter.socket(
            path,
            channels=[snapshot],
            name=self._endpoint_name(path, name),
            path_model=path_model,
            subprotocol=subprotocol,
            summary=summary,
        )
        self._include_websocket_adapter(adapter, path, dependencies)

    def channel(
        self,
        path: str,
        *,
        namespace: str | None = None,
        name: str | None = None,
        dependencies: Sequence[Depends] = (),
        path_model: type[BaseModel] | None = None,
        subprotocol: str | None = None,
        summary: str | None = None,
        **options: Unpack[ChannelOptions],
    ) -> RpcChannel:
        """Declare a JSON-RPC WebSocket endpoint and return its channel.

        Register methods, events and message streams on the returned channel.
        Operation names use the router's ``namespace`` unless ``namespace`` is
        given. The channel is mounted when the router's routes are first read,
        for example by ``include_router``. It is frozen afterwards, so later
        registrations fail instead of being silently ignored. Use
        ``include_channel`` to mount one channel at several paths.
        """
        from fastapi_canon.integrations.websockets import RpcChannel

        channel = RpcChannel(
            namespace=self.namespace if namespace is None else namespace
        )
        settings: dict[str, Any] = {
            **options,
            "path": path,
            "name": name,
            "dependencies": dependencies,
            "path_model": path_model,
            "subprotocol": subprotocol,
            "summary": summary,
        }
        self._pending_channels.append((channel, settings))
        return channel

    def binary_stream[Stream: Callable[..., Any]](
        self,
        path: str,
        *,
        content_type: str | None = None,
        input_content_type: str | None = None,
        summary: str | None = None,
        name: str | None = None,
        dependencies: Sequence[Depends] = (),
        subprotocol: str | None = None,
        **options: Unpack[ChannelOptions],
    ) -> Callable[[Stream], Stream]:
        """Declare a binary stream on its own WebSocket endpoint.

        The signature sets the direction: an ``AsyncIterator[bytes]`` generator
        sends frames, injected ``RpcBinaryInput``/``RpcBinaryOutput`` receive and
        send them. The operation name is the router's ``namespace`` and the
        function name. Connection options match ``include_channel``.
        """
        from fastapi_canon.integrations.websockets import (
            CanonWebSocketRouter,
            RpcChannel,
        )

        def decorate(stream: Stream) -> Stream:
            channel = RpcChannel(namespace=self.namespace)
            declare: Any = channel.stream(
                content_type=content_type,
                input_content_type=input_content_type,
                summary=summary,
            )
            declare(stream)
            adapter = CanonWebSocketRouter(prefix=self.prefix, **options)
            adapter.stream(
                path,
                stream,
                name=self._endpoint_name(path, name),
                subprotocol=subprotocol,
            )
            self._include_websocket_adapter(adapter, path, dependencies)
            return stream

        return decorate

    def socket[Handler: Callable[..., Any]](
        self,
        path: str,
        *,
        protocol: RpcSocketProtocol | None = None,
        messages: Sequence[RpcMessageType] | None = None,
        closes: Sequence[RpcErrorBinding[Any]] = (),
        summary: str | None = None,
        name: str | None = None,
        dependencies: Sequence[Depends] = (),
        subprotocol: str | None = None,
        **options: Unpack[ChannelOptions],
    ) -> Callable[[Handler], Handler]:
        """Declare a message socket on its own WebSocket endpoint.

        A handler taking a message and returning a response validates JSON from
        its annotations. Inject ``RpcSocket[Incoming, Outgoing]`` for a typed
        connection loop, or bare ``RpcSocket`` to forward text and binary frames
        unchanged. The operation name is the router's ``namespace`` and the
        function name. Connection options match ``include_channel``.
        """
        from fastapi_canon.integrations.websockets import (
            CanonWebSocketRouter,
            RpcChannel,
        )

        def decorate(handler: Handler) -> Handler:
            channel = RpcChannel(namespace=self.namespace)
            declaration: dict[str, Any] = {
                "protocol": protocol,
                "closes": closes,
                "summary": summary,
            }
            if messages is not None:
                declaration["messages"] = messages
            declare: Any = channel.socket(**declaration)
            declare(handler)
            adapter = CanonWebSocketRouter(prefix=self.prefix, **options)
            adapter.socket(
                path,
                handler,
                name=self._endpoint_name(path, name),
                subprotocol=subprotocol,
            )
            self._include_websocket_adapter(adapter, path, dependencies)
            return handler

        return decorate

    def sse[Endpoint: Callable[..., Any]](
        self, path: str, **options: Unpack[StreamOptions]
    ) -> Callable[[Endpoint], Endpoint]:
        """Declare typed SSE with serialization and terminal completion/problems."""
        from fastapi_canon.integrations.sse.canon import CanonStreamRouter

        adapter = CanonStreamRouter(
            namespace=self.namespace,
            generate_unique_id_function=self.generate_unique_id_function,
            raises=self.raises,
            http_statuses=self.http_statuses,
        )
        register = adapter.stream(path, **options)

        def decorate(endpoint: Endpoint) -> Endpoint:
            # Inject the generator itself, so resolution failures become
            # terminal problems like other processing failures.
            if issubclass(self.route_class, InjectedRoute):
                register(inject_endpoint(endpoint))
            else:
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
        **options: Unpack[SessionOptions],
    ) -> Callable[[Factory], Factory]:
        """Declare a WebRTC session factory with managed signaling and cleanup.

        A factory may also supply its session's engine.
        """
        from fastapi_canon.integrations.webrtc.canon import CanonWebRtcRouter

        adapter = CanonWebRtcRouter(
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

    def _endpoint_name(self, path: str, name: str | None) -> str:
        return name if name is not None else _channel_name(self.prefix + path)

    def _include_websocket_adapter(
        self, adapter: Any, path: str, dependencies: Sequence[Depends]
    ) -> None:
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
        self._included_error_registries.extend(error_registries(adapter))


def _websocket_path(path: str) -> str:
    return re.sub(r"\{[^}:]+(?=[:}])", "{parameter", path)


def _channel_name(path: str) -> str:
    value = re.sub(r"[{}]", "", path)
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return f"rpc_{value}" if value else "rpc_root"
