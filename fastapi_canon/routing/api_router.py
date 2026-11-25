from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, TypedDict, Unpack

from fastapi import APIRouter as FastAPIRouter
from fastapi import WebSocket
from fastapi.params import Depends
from fastapi.routing import APIWebSocketRoute
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel, create_model
from starlette.routing import BaseRoute, WebSocketRoute

from fastapi_canon.error.contributions import error_registries
from fastapi_canon.errors import ConfigurationError, ResponseConfigurationError
from fastapi_canon.routing.http import APIRouteOptions as APIRouteOptions
from fastapi_canon.routing.http import CanonRouter
from fastapi_canon.routing.http import IncludeRouterOptions as IncludeRouterOptions
from fastapi_canon.routing.http import RouteOptions as RouteOptions
from fastapi_canon.routing.http import RouterOptions as RouterOptions
from fastapi_canon.routing.injected_route import InjectedRoute, inject_endpoint
from fastapi_canon.routing.traversal import iter_contract_routes

if TYPE_CHECKING:
    from fastapi_canon.error import AnyError
    from fastapi_canon.integrations.websockets import (
        RpcBeforeAccept,
        RpcChannel,
        RpcDrain,
        RpcErrorMapper,
        RpcLimits,
        RpcMessageType,
        RpcMiddlewareLike,
        RpcProtocol,
        RpcRejectionDeclaration,
        RpcRejections,
        RpcResolverLike,
        RpcSocketProtocol,
        WebSocketPolicy,
    )
    from fastapi_canon.integrations.websockets.adapters.fastapi import (
        FastApiResolver,
    )
    from fastapi_canon.response import CanonResponse


class ChannelOptions(TypedDict, total=False):
    """Connection options for ``include_channel`` and the socket decorators."""

    version: int
    context: Callable[..., Awaitable[Any]] | None
    resolver: RpcResolverLike | FastApiResolver | None
    hooks: Sequence[Callable[..., Any]]
    rejects: Sequence[RpcRejectionDeclaration]
    rejections: RpcRejections | None
    middleware: Sequence[RpcMiddlewareLike]
    error_mapper: RpcErrorMapper | None
    limits: RpcLimits | None
    strict_errors: bool
    drain: RpcDrain | None
    before_accept: RpcBeforeAccept | None


class APIRouter(CanonRouter):
    """Compose HTTP, SSE, WebSocket channels, streams and sockets by feature.

    HTTP and SSE endpoints resolve ``Inject[T]`` and ``FromDishka[T]`` parameters
    without ``route_class=DishkaRoute`` or ``@inject``. An explicit
    ``route_class`` replaces this behavior.

    ``websocket_policy`` configures the connections of this router's channels,
    sockets and binary streams. Such a router rejects WebSocket
    routes, also included ones, that were not declared under its policy or an
    extension of it.
    """

    def __init__(
        self,
        *,
        websocket_policy: WebSocketPolicy | None = None,
        **kwargs: Unpack[RouterOptions],
    ) -> None:
        raw_policy: object = websocket_policy
        if raw_policy is not None:
            from fastapi_canon.integrations.websockets import WebSocketPolicy

            if not isinstance(raw_policy, WebSocketPolicy):
                msg = "websocket_policy must be a WebSocketPolicy instance or None"
                raise ConfigurationError(msg)
        kwargs.setdefault("route_class", InjectedRoute)
        self.websocket_policy = websocket_policy
        self._required_websocket_policies: list[WebSocketPolicy] = []
        self._websocket_children: list[APIRouter] = []
        super().__init__(**kwargs)

    @property
    def routes(self) -> list[BaseRoute]:
        requirements = self._websocket_requirements()
        if requirements:
            # Native included routers stay live and cannot intercept registration.
            # Revalidate before inspection or dispatch, including cached inclusions.
            for path, route in iter_contract_routes(self._routes):
                if isinstance(getattr(route, "original_route", route), WebSocketRoute):
                    self._check_websocket_route(path, route.endpoint, requirements)
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
        self._check_websocket_route(
            self.prefix + path,
            endpoint,
            self._websocket_requirements(),
        )
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

    def add_websocket_route(
        self,
        path: str,
        endpoint: Callable[[WebSocket], Awaitable[None]],
        name: str | None = None,
    ) -> None:
        self._check_websocket_route(path, endpoint, self._websocket_requirements())
        super().add_websocket_route(path, endpoint, name=name)

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
        requirements = self._websocket_requirements()
        # Reading routes first mounts the child's pending channels.
        routes = router.routes
        for path, route in iter_contract_routes(routes):
            if not isinstance(getattr(route, "original_route", route), WebSocketRoute):
                continue
            key = _websocket_path(prefix + path)
            if key in existing or key in incoming:
                raise ConfigurationError(f"Duplicate WebSocket path: {prefix + path}")
            self._check_websocket_route(prefix + path, route.endpoint, requirements)
            incoming.add(key)
        super().include_router(router, **options)
        if isinstance(router, APIRouter):
            self._websocket_children.append(router)
            for policy in requirements:
                router._require_websocket_policy(policy)

    def include_channel(
        self,
        channel: RpcChannel,
        *,
        path: str,
        name: str | None = None,
        dependencies: Sequence[Depends] = (),
        subprotocol: str | None = None,
        summary: str | None = None,
        close_when_events_complete: bool = False,
        **options: Unpack[ChannelOptions],
    ) -> None:
        """Mount a channel snapshot at one JSON-RPC WebSocket endpoint.

        Operations keep the channel's namespace. Parameters annotated with
        ``Path()`` read this endpoint's path variables, validated at handshake.
        ``close_when_events_complete`` closes the connection once every event
        source has finished.
        Endpoint names default to the complete local path, including the prefix.
        The source channel stays reusable and mutable after registration.
        """
        from fastapi_canon.integrations.websockets import RpcChannel
        from fastapi_canon.integrations.websockets.adapters.fastapi import (
            CanonWebSocketRouter,
        )

        if not isinstance(channel, RpcChannel):
            raise ConfigurationError("include_channel expects an RpcChannel")
        snapshot = RpcChannel()
        snapshot.include_channel(channel)
        protocol = snapshot.freeze()
        if protocol.streams or protocol.sockets:
            raise ConfigurationError(
                "include_channel accepts JSON-RPC methods, events and message streams; "
                "declare binary streams with @router.binary_stream and sockets with @router.socket"
            )
        adapter = CanonWebSocketRouter(
            prefix=self.prefix, **self._connection_options(options)
        )
        adapter.socket(
            path,
            channels=[snapshot],
            name=self._endpoint_name(path, name),
            path_model=_channel_path_model(protocol, self.prefix + path),
            subprotocol=subprotocol,
            summary=summary,
            close_when_events_complete=close_when_events_complete,
        )
        self._include_websocket_adapter(adapter, path, dependencies)

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
        from fastapi_canon.integrations.websockets import RpcChannel
        from fastapi_canon.integrations.websockets.adapters.fastapi import (
            CanonWebSocketRouter,
        )

        connection = self._connection_options(options)

        def decorate(stream: Stream) -> Stream:
            channel = RpcChannel(namespace=self.namespace or "")
            declare: Any = channel.stream(
                content_type=content_type,
                input_content_type=input_content_type,
                summary=summary,
            )
            declare(stream)
            adapter = CanonWebSocketRouter(prefix=self.prefix, **connection)
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
        closes: Sequence[RpcRejectionDeclaration] = (),
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
        from fastapi_canon.integrations.websockets import RpcChannel
        from fastapi_canon.integrations.websockets.adapters.fastapi import (
            CanonWebSocketRouter,
        )
        from fastapi_canon.integrations.websockets.channel import declare_socket

        connection = self._connection_options(options)

        def decorate(handler: Handler) -> Handler:
            channel = RpcChannel(namespace=self.namespace or "")
            declaration: dict[str, Any] = {
                "protocol": protocol,
                "closes": closes,
                "summary": summary,
            }
            if messages is not None:
                declaration["messages"] = messages
            declare_socket(channel, handler, **declaration)
            adapter = CanonWebSocketRouter(prefix=self.prefix, **connection)
            adapter.socket(
                path,
                handler,
                name=self._endpoint_name(path, name),
                subprotocol=subprotocol,
            )
            self._include_websocket_adapter(adapter, path, dependencies)
            return handler

        return decorate

    def api_route[CallableT: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError] = (),
        http_statuses: Sequence[int] = (),
        response: CanonResponse | None = None,
        **kwargs: Unpack[APIRouteOptions],
    ) -> Callable[[CallableT], CallableT]:
        """Declare a route; ``response_class=EventSourceResponse`` declares typed SSE.

        A stream yields a concrete JSON event type, ends with ``canon.complete``
        and reports failures as a terminal ``canon.problem`` event.
        """
        response_class = kwargs.get("response_class")
        if not (
            isinstance(response_class, type)
            and issubclass(response_class, EventSourceResponse)
        ):
            return super().api_route(
                path,
                raises=raises,
                http_statuses=http_statuses,
                response=response,
                **kwargs,
            )
        if response is not None:
            raise ResponseConfigurationError(
                f"stream {path!r} infers its event contract; remove response="
            )
        return self._stream(
            path, raises=raises, http_statuses=http_statuses, options=kwargs
        )

    def _stream[Endpoint: Callable[..., Any]](
        self,
        path: str,
        *,
        raises: Sequence[AnyError],
        http_statuses: Sequence[int],
        options: APIRouteOptions,
    ) -> Callable[[Endpoint], Endpoint]:
        from fastapi_canon.integrations.sse.canon import CanonStreamRouter

        adapter = CanonStreamRouter(
            namespace=self.namespace,
            generate_unique_id_function=self.generate_unique_id_function,
            raises=self.raises,
            http_statuses=self.http_statuses,
        )
        register = adapter.stream(
            path, raises=raises, http_statuses=http_statuses, **options
        )

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

    def _connection_options(self, options: ChannelOptions) -> dict[str, Any]:
        from fastapi_canon.integrations.websockets.policy import (
            apply_websocket_policy,
        )

        return apply_websocket_policy(self.websocket_policy, options)

    def _websocket_requirements(self) -> list[WebSocketPolicy]:
        own = [] if self.websocket_policy is None else [self.websocket_policy]
        return [*own, *self._required_websocket_policies]

    def _require_websocket_policy(self, policy: WebSocketPolicy) -> None:
        # Included routers stay live, so later declarations must also comply.
        self._required_websocket_policies.append(policy)
        for child in self._websocket_children:
            child._require_websocket_policy(policy)

    def _check_websocket_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        requirements: Sequence[WebSocketPolicy],
    ) -> None:
        if not requirements:
            return
        from fastapi_canon.integrations.websockets.policy import (
            WebSocketPolicy,
            covers_websocket_policy,
        )

        declared = getattr(endpoint, "__canon_websocket_policy__", None)
        if not isinstance(declared, WebSocketPolicy):
            declared = None
        for required in requirements:
            if not covers_websocket_policy(declared, required):
                msg = (
                    f"WebSocket route {path} is not declared under the "
                    "websocket_policy of the router that includes it; declare it "
                    "on a router with that policy or policy.extend(...)"
                )
                raise ConfigurationError(msg)

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
        if self.websocket_policy is not None:
            route.endpoint.__dict__["__canon_websocket_policy__"] = (
                self.websocket_policy
            )
        self.add_api_websocket_route(
            path,
            route.endpoint,
            name=route.name,
            dependencies=[*dependencies, *route.dependencies],
        )
        self._included_error_registries.extend(error_registries(adapter))


def _channel_path_model(protocol: RpcProtocol, path: str) -> type[BaseModel] | None:
    """Combine the operations' ``Path()`` parameters into one handshake model."""
    variables = re.findall(r"\{([^}:]+)(?::[^}]*)?\}", path)
    fields: dict[str, Any] = {}
    owners: dict[str, str] = {}
    operations: list[tuple[str, type[BaseModel] | None]] = [
        *((method.name, method.path_model) for method in protocol.methods),
        *((stream.name, stream.path_model) for stream in protocol.subscriptions),
    ]
    for operation, path_model in operations:
        if path_model is None:
            continue
        for field_name, field in path_model.model_fields.items():
            if field_name not in variables:
                msg = (
                    f"RPC operation {operation} reads path parameter "
                    f"{field_name!r}, which is not a variable of {path!r}"
                )
                raise ConfigurationError(msg)
            previous = fields.setdefault(field_name, (field.annotation, field))
            if previous[0] != field.annotation:
                msg = (
                    f"RPC operations {owners[field_name]} and {operation} "
                    f"declare path parameter {field_name!r} with different types"
                )
                raise ConfigurationError(msg)
            previous_alias = previous[1].validation_alias
            alias = field.validation_alias
            previous_input = field_name if previous_alias is None else previous_alias
            field_input = field_name if alias is None else alias
            if previous_input != field_input:
                first, second = sorted((owners[field_name], operation))
                msg = (
                    f"RPC operations {first} and {second} declare path parameter "
                    f"{field_name!r} with different input aliases"
                )
                raise ConfigurationError(msg)
            if previous[1].metadata != field.metadata:
                msg = (
                    f"RPC operations {owners[field_name]} and {operation} "
                    f"declare path parameter {field_name!r} with different constraints"
                )
                raise ConfigurationError(msg)
            owners.setdefault(field_name, operation)
    if not fields:
        return None
    for variable in variables:
        fields.setdefault(variable, (str, ...))
    model: type[BaseModel] = create_model("ChannelPathParams", **fields)
    return model


def _websocket_path(path: str) -> str:
    return re.sub(r"\{[^}:]+(?=[:}])", "{parameter", path)


def _channel_name(path: str) -> str:
    value = re.sub(r"[{}]", "", path)
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return f"rpc_{value}" if value else "rpc_root"
