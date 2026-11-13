import inspect
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from math import isfinite
from types import FunctionType
from typing import Any, overload

from fastapi_canon.error import Error, ErrorRegistry
from fastapi_canon.integrations.websockets.connection import RpcLimits
from fastapi_canon.integrations.websockets.dependencies import (
    RpcResolverLike,
    RpcResolverScope,
    call_scope,
    resolver_with_context,
)
from fastapi_canon.integrations.websockets.errors import (
    ProtocolDefinitionError,
    RpcError,
    RpcErrorBinding,
    RpcErrorDeclaration,
    merge_error_declarations,
    rejecting_contracts,
)
from fastapi_canon.integrations.websockets.middleware import (  # smelt: ignore[SMT104] -- types  # noqa: E501
    # Channel factories and serving ports share forward-only service annotations.
    RpcMiddlewareLike,
)
from fastapi_canon.integrations.websockets.protocol import (
    RpcNotificationDefinition,
    RpcProtocol,
    RpcSocketDefinition,
    RpcStreamDefinition,
    RpcSubscriptionDefinition,
    method_definition,
    notification_definition,
    notification_type_definitions,
    socket_definition,
    stream_definition,
    stream_payload,
    subscription_definition,
)
from fastapi_canon.integrations.websockets.server import (
    RpcErrorMapper,
    RpcServer,
)
from fastapi_canon.integrations.websockets.sockets import (
    RpcMessageType,
    RpcSocketProtocol,
)
from fastapi_canon.integrations.websockets.streams import (
    RpcBinaryInput,
    RpcBinaryOutput,
)


class _TimeoutDefault(Enum):
    INHERIT = "inherit"


_INHERIT_TIMEOUT = _TimeoutDefault.INHERIT


@dataclass(frozen=True, slots=True)
class RpcRoute:
    name: str
    function: FunctionType
    summary: str | None
    raises: tuple[type[RpcError], ...]
    resolver_scope: RpcResolverScope
    timeout: float | None = None
    _has_timeout: bool = False
    idempotent: bool = False

    @property
    def has_timeout(self) -> bool:
        """Whether the declaration overrides the inherited request timeout."""
        return self._has_timeout


class RpcChannel:
    """Methods, events, streams and sockets the server implements."""

    def __init__(
        self,
        name: str | None = None,
        /,
        *,
        namespace: str | None = None,
        raises: Iterable[RpcErrorDeclaration] = (),
        resolver_scope: RpcResolverScope = call_scope,
    ) -> None:
        resolved_namespace = (
            (normalize_namespace(name) if name is not None else "")
            if namespace is None
            else normalize_namespace(namespace)
        )
        self._name = (
            normalize_namespace(name)
            if name is not None
            else resolved_namespace or "channel"
        )
        if not self._name:
            raise ProtocolDefinitionError("RPC channel name cannot be empty")
        self._namespace = resolved_namespace
        # Collected from raises; Composition merges it into the application catalog.
        self.error_registry: ErrorRegistry | None = None
        self._raises: tuple[type[RpcError], ...] = ()
        self._raises = self.merge_raises(raises, owner=f"RPC channel {self.name}")
        if not callable(resolver_scope):
            raise ProtocolDefinitionError("RPC resolver scope must be callable")
        self._resolver_scope = resolver_scope
        self._routes: list[RpcRoute] = []
        self._events: list[RpcNotificationDefinition] = []
        self._subscriptions: list[RpcSubscriptionDefinition] = []
        self._streams: list[RpcStreamDefinition] = []
        self._sockets: list[RpcSocketDefinition] = []
        self._children: list[RpcChannel] = []
        self._names: set[str] = set()
        self._protocol: RpcProtocol | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def raises(self) -> tuple[type[RpcError], ...]:
        return self._raises

    @property
    def resolver_scope(self) -> RpcResolverScope:
        return self._resolver_scope

    @property
    def routes(self) -> tuple[RpcRoute, ...]:
        return tuple(self._routes)

    @property
    def events(self) -> tuple[RpcNotificationDefinition, ...]:
        return tuple(self._events)

    @property
    def subscriptions(self) -> tuple[RpcSubscriptionDefinition, ...]:
        return tuple(self._subscriptions)

    @property
    def streams(self) -> tuple[RpcStreamDefinition, ...]:
        return tuple(self._streams)

    @property
    def sockets(self) -> tuple[RpcSocketDefinition, ...]:
        return tuple(self._sockets)

    @property
    def children(self) -> tuple["RpcChannel", ...]:
        return tuple(self._children)

    @property
    def protocol(self) -> RpcProtocol:
        return self.freeze()

    @overload
    def method[Handler: Callable[..., Any]](self, function: Handler, /) -> Handler: ...

    @overload
    def method[Handler: Callable[..., Any]](
        self,
        name: str | None = None,
        /,
        *,
        summary: str | None = None,
        raises: Iterable[RpcErrorDeclaration] = (),
        timeout: float | _TimeoutDefault | None = _INHERIT_TIMEOUT,
        idempotent: bool = False,
    ) -> Callable[[Handler], Handler]: ...

    def method(
        self,
        name: str | Callable[..., Any] | None = None,
        /,
        *,
        summary: str | None = None,
        raises: Iterable[RpcErrorDeclaration] = (),
        timeout: float | _TimeoutDefault | None = _INHERIT_TIMEOUT,
        idempotent: bool = False,
    ) -> Any:
        channel = self
        channel.ensure_mutable()
        if type(idempotent) is not bool:
            raise ProtocolDefinitionError("Method idempotent must be a bool")
        has_timeout = timeout is not _INHERIT_TIMEOUT
        if (
            has_timeout
            and timeout is not None
            and (
                not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
                or not isfinite(timeout)
                or timeout <= 0
            )
        ):
            raise ProtocolDefinitionError(
                "Method timeout must be finite and positive or None"
            )
        if isinstance(name, FunctionType):
            return self.method()(name)
        if name is not None and not isinstance(name, str):
            raise ProtocolDefinitionError(
                "RPC method decorator expects a function or name"
            )
        local_name = None if name is None else _name(name, "method name")
        raises = tuple(raises)

        def decorate(function: FunctionType) -> FunctionType:
            channel.validate_function(function, "method", coroutine=True)
            wire_name = join_rpc_name(
                channel.namespace, local_name or function.__name__
            )
            merged_raises = channel.merge_raises(
                raises,
                owner=f"RPC method {wire_name}",
            )
            channel.reserve_name(wire_name)
            channel._routes.append(
                RpcRoute(
                    wire_name,
                    function,
                    summary or _docstring_summary(function),
                    merged_raises,
                    channel.resolver_scope,
                    timeout if isinstance(timeout, (int, float)) else None,
                    has_timeout,
                    idempotent,
                )
            )
            return function

        return decorate

    @overload
    def event(self, function: FunctionType, /) -> FunctionType: ...

    @overload
    def event(
        self,
        name: str | None = None,
        /,
        *,
        payload: Any = None,
        summary: str | None = None,
        on_error: str = "continue",
        raises: Iterable[RpcErrorDeclaration] = (),
    ) -> Callable[[FunctionType], FunctionType]: ...

    def event(
        self,
        name: str | FunctionType | None = None,
        /,
        *,
        payload: Any = None,
        summary: str | None = None,
        on_error: str = "continue",
        raises: Iterable[RpcErrorDeclaration] = (),
    ) -> Any:
        channel = self
        channel.ensure_mutable()
        if isinstance(name, FunctionType):
            return self.event()(name)
        if on_error not in ("continue", "close"):
            raise ProtocolDefinitionError(
                "RPC event on_error must be 'continue' or 'close'"
            )
        if name is not None and not isinstance(name, str):
            raise ProtocolDefinitionError(
                "RPC event decorator expects a function or name"
            )
        raises = tuple(raises)

        def decorate(function: FunctionType) -> FunctionType:
            channel.validate_function(function, "event", coroutine=False)
            wire_name = join_rpc_name(
                channel.namespace, _name(name or function.__name__, "event name")
            )
            merged_raises = channel.merge_raises(
                raises,
                owner=f"RPC event {wire_name}",
            )
            definition = notification_definition(
                name=wire_name,
                payload=payload,
                function=function,
                summary=summary or _docstring_summary(function),
                server=None,
                on_error=on_error,
                raises=merged_raises,
            )
            channel.reserve_name(wire_name)
            channel._events.append(definition)
            return function

        return decorate

    @overload
    def stream(self, function: FunctionType, /) -> FunctionType: ...

    @overload
    def stream(
        self,
        name: str | None = None,
        /,
        *,
        content_type: str | None = None,
        input_content_type: str | None = None,
        summary: str | None = None,
        raises: Iterable[RpcErrorDeclaration] = (),
    ) -> Callable[[FunctionType], FunctionType]: ...

    def stream(
        self,
        name: str | FunctionType | None = None,
        /,
        *,
        content_type: str | None = None,
        input_content_type: str | None = None,
        summary: str | None = None,
        raises: Iterable[RpcErrorDeclaration] = (),
    ) -> Any:
        channel = self
        channel.ensure_mutable()
        if isinstance(name, FunctionType):
            return self.stream()(name)
        if name is not None and not isinstance(name, str):
            raise ProtocolDefinitionError(
                "RPC stream decorator expects a function or name"
            )
        if content_type is not None and (
            not isinstance(content_type, str) or not content_type
        ):
            raise ProtocolDefinitionError("RPC stream content_type cannot be empty")
        if input_content_type is not None and (
            not isinstance(input_content_type, str) or not input_content_type
        ):
            raise ProtocolDefinitionError(
                "RPC stream input_content_type cannot be empty"
            )

        raises = tuple(raises)

        def decorate(function: FunctionType) -> FunctionType:
            channel.validate_function(function, "stream", coroutine=False)
            local = _name(name or function.__name__, "stream name")
            wire_name = join_rpc_name(channel.namespace, local)
            if (
                inspect.isasyncgenfunction(function)
                and stream_payload(function) is not bytes
            ):
                if content_type is not None or input_content_type is not None:
                    raise ProtocolDefinitionError(
                        "Content types apply only to binary streams"
                    )
                merged_raises = channel.merge_raises(
                    raises,
                    owner=f"RPC stream {wire_name}",
                )
                message_stream = subscription_definition(
                    name=wire_name,
                    function=function,
                    summary=summary or _docstring_summary(function),
                    resolver_scope=channel.resolver_scope,
                    raises=merged_raises,
                )
                if any(
                    item.dependency in (RpcBinaryInput, RpcBinaryOutput)
                    for item in message_stream.injected_parameters
                ):
                    raise ProtocolDefinitionError(
                        "Message streams cannot inject binary ports"
                    )
                channel.reserve_name(wire_name)
                channel._subscriptions.append(message_stream)
                return function
            if raises:
                raise ProtocolDefinitionError(
                    "Binary stream errors use endpoint rejections, not raises"
                )
            definition = stream_definition(
                name=wire_name,
                function=function,
                content_type=content_type or "application/octet-stream",
                input_content_type=input_content_type,
                summary=summary or _docstring_summary(function),
                resolver_scope=channel.resolver_scope,
            )
            channel.reserve_name(wire_name)
            channel._streams.append(definition)
            function.__dict__["__rpckit_stream__"] = channel, local
            return function

        return decorate

    @overload
    def socket(self, function: FunctionType, /) -> FunctionType: ...

    @overload
    def socket(
        self,
        name: str | None = None,
        /,
        *,
        protocol: RpcSocketProtocol | None = None,
        messages: Iterable[RpcMessageType] = (
            RpcMessageType.TEXT,
            RpcMessageType.BINARY,
        ),
        closes: Iterable[RpcErrorBinding[Any]] = (),
        summary: str | None = None,
    ) -> Callable[[FunctionType], FunctionType]: ...

    def socket(
        self,
        name: str | FunctionType | None = None,
        /,
        *,
        protocol: RpcSocketProtocol | None = None,
        messages: Iterable[RpcMessageType] = (
            RpcMessageType.TEXT,
            RpcMessageType.BINARY,
        ),
        closes: Iterable[RpcErrorBinding[Any]] = (),
        summary: str | None = None,
    ) -> Any:
        """Declare a WebSocket endpoint with a message or connection handler.

        A message handler receives its first non-injected parameter as validated
        JSON and returns one response per message. Inject ``RpcSocket`` instead
        to manage the connection. ``RpcSocket[Incoming, Outgoing]`` infers JSON
        validation from its type arguments; bare ``RpcSocket`` forwards text and
        binary frames unchanged. Message types come only from annotations.

        ``protocol`` declares the application protocol the messages follow, for
        the contract and generated client bindings; it never changes how messages
        are handled. ``closes`` binds the exceptions the handler may raise to the
        close code and reason the client receives.
        """
        channel = self
        channel.ensure_mutable()
        if isinstance(name, FunctionType):
            return self.socket()(name)
        if name is not None and not isinstance(name, str):
            raise ProtocolDefinitionError(
                "RPC socket decorator expects a function or name"
            )
        if protocol is not None and not isinstance(protocol, RpcSocketProtocol):
            raise ProtocolDefinitionError(
                "RPC socket protocol must be an RpcSocketProtocol"
            )
        try:
            declared = frozenset(RpcMessageType(item) for item in messages)
        except ValueError as error:
            raise ProtocolDefinitionError(
                "RPC socket messages must be RpcMessageType values"
            ) from error
        if not declared:
            raise ProtocolDefinitionError("RPC socket needs at least one message type")
        message_types = tuple(item for item in RpcMessageType if item in declared)

        def decorate(function: FunctionType) -> FunctionType:
            channel.validate_function(function, "socket", coroutine=False)
            local = _name(name or function.__name__, "socket name")
            wire_name = join_rpc_name(channel.namespace, local)
            definition = socket_definition(
                name=wire_name,
                function=function,
                messages=message_types,
                protocol=protocol,
                closes=rejecting_contracts(closes, f"RPC socket {wire_name}"),
                summary=summary or _docstring_summary(function),
                resolver_scope=channel.resolver_scope,
            )
            channel.reserve_name(wire_name)
            channel._sockets.append(definition)
            function.__dict__["__rpckit_socket__"] = channel, local
            return function

        return decorate

    def child(
        self,
        name: str,
        /,
        *,
        raises: Iterable[RpcErrorDeclaration] = (),
        resolver_scope: RpcResolverScope | None = None,
    ) -> "RpcChannel":
        self.ensure_mutable()
        local = _name(name, "child channel name")
        child = RpcChannel(
            join_rpc_name(self.name, local),
            namespace=join_rpc_name(self.namespace, local),
            resolver_scope=resolver_scope or self.resolver_scope,
        )
        child.error_registry = self.error_registry
        child._raises = self.raises
        child._raises = child.merge_raises(raises, owner=f"RPC channel {child.name}")
        self._children.append(child)
        return child

    def include_channel(self, channel: "RpcChannel", *, namespace: str = "") -> None:
        """Include an independent snapshot under a relative namespace.

        The namespace replaces the included channel's root namespace. Nested
        child namespaces, handler functions, error mappings and scopes are kept.
        Later registrations on the original channel do not change this snapshot.
        """
        self.ensure_mutable()
        if not isinstance(channel, RpcChannel):
            raise ProtocolDefinitionError("include_channel expects an RpcChannel")
        if channel is self:
            raise ProtocolDefinitionError("A channel cannot include itself")
        if not isinstance(namespace, str):
            raise ProtocolDefinitionError("RPC namespace must be a string")
        namespace = normalize_namespace(namespace)
        snapshot = channel.snapshot(join_rpc_name(self.namespace, namespace))
        names = _channel_names(self)
        included = _channel_names(snapshot)
        duplicates = names.intersection(included)
        if duplicates:
            raise ProtocolDefinitionError(
                f"Duplicate RPC route: {', '.join(sorted(duplicates))}"
            )
        combined = names | included
        for name in combined:
            segments = name.split(".")
            if any(".".join(segments[:i]) in combined for i in range(1, len(segments))):
                raise ProtocolDefinitionError(
                    f"RPC name {name!r} conflicts with an operation namespace"
                )
        controls = _channel_controls(self) | _channel_controls(snapshot)
        if overlap := controls.intersection(combined):
            raise ProtocolDefinitionError(
                f"RPC stream control collides with an operation: {sorted(overlap)[0]}"
            )
        self._children.append(snapshot)

    def freeze(self) -> RpcProtocol:
        if self._protocol is None:
            definitions = [
                method_definition(
                    name=r.name,
                    function=r.function,
                    handler_name=r.function.__name__,
                    summary=r.summary,
                    raises=r.raises,
                    server=None,
                    resolver_scope=r.resolver_scope,
                    timeout=r.timeout,
                    has_timeout=r.has_timeout,
                    idempotent=r.idempotent,
                )
                for r in self.routes
            ]
            notifications = list(self.events)
            subscriptions = list(self.subscriptions)
            notification_types = [
                item
                for event in self.events
                for item in notification_type_definitions(event.payload)
            ]
            streams = list(self.streams)
            sockets = list(self.sockets)
            for child in self.children:
                protocol = child.freeze()
                definitions.extend(protocol.methods)
                notifications.extend(protocol.notifications)
                subscriptions.extend(protocol.subscriptions)
                notification_types.extend(protocol.notification_types)
                streams.extend(protocol.streams)
                sockets.extend(protocol.sockets)
            duplicate_requests = {
                name
                for name, count in Counter(d.request_name for d in definitions).items()
                if count > 1
            }
            definitions = [
                replace(
                    d,
                    request_name=request_name(d.name)
                    if d.request_name in duplicate_requests
                    else d.request_name,
                )
                for d in definitions
            ]
            self._protocol = RpcProtocol(
                methods=definitions,
                notifications=notifications,
                subscriptions=subscriptions,
                notification_types=notification_types,
                streams=streams,
                sockets=sockets,
            )
        return self._protocol

    def create_server(
        self,
        *,
        context: object | Mapping[type[Any], object] | None = None,
        resolver: RpcResolverLike | None = None,
        error_mapper: RpcErrorMapper | None = None,
        limits: RpcLimits | None = None,
        strict_errors: bool = False,
        middleware: Sequence[RpcMiddlewareLike] = (),
    ) -> RpcServer:
        return RpcServer.from_channel(
            self.protocol,
            resolver=resolver_with_context(resolver, context),
            error_mapper=error_mapper,
            limits=limits,
            strict_errors=strict_errors,
            middleware=middleware,
        )

    def merge_raises(
        self, raises: Iterable[RpcErrorDeclaration], *, owner: str
    ) -> tuple[type[RpcError], ...]:
        declarations = tuple(raises)
        canonical = tuple(error for error in declarations if isinstance(error, Error))
        registry = self.error_registry
        if canonical:
            registry = ErrorRegistry.merge(
                *((registry,) if registry is not None else ()),
                ErrorRegistry(errors=canonical),
                type_base=registry.type_base if registry is not None else None,
            )
        result = merge_error_declarations(
            self.raises, declarations, owner=owner, registry=registry
        )
        self.error_registry = registry
        return result

    def reserve_name(self, name: str) -> None:
        included_controls = set().union(
            *(_channel_controls(child) for child in self.children)
        )
        if name in _channel_names(self) or name in included_controls:
            raise ProtocolDefinitionError(f"Duplicate RPC route: {name}")
        self._names.add(name)

    def snapshot(self, namespace: str) -> "RpcChannel":
        snapshot = RpcChannel(
            self.name, namespace=namespace, resolver_scope=self.resolver_scope
        )
        snapshot.error_registry = self.error_registry
        snapshot._raises = self.raises

        def rename(name: str) -> str:
            local = name[len(self.namespace) + 1 :] if self.namespace else name
            return join_rpc_name(namespace, local)

        snapshot._routes = [
            replace(route, name=rename(route.name)) for route in self.routes
        ]
        snapshot._events = [
            replace(event, name=rename(event.name)) for event in self.events
        ]
        snapshot._subscriptions = [
            replace(stream, name=rename(stream.name)) for stream in self.subscriptions
        ]
        snapshot._streams = [
            replace(stream, name=rename(stream.name)) for stream in self.streams
        ]
        snapshot._sockets = [
            replace(socket, name=rename(socket.name)) for socket in self.sockets
        ]
        snapshot._names = {rename(name) for name in self._names}
        snapshot._children = [
            child.snapshot(rename(child.namespace)) for child in self.children
        ]
        return snapshot

    def validate_function(self, function: Any, kind: str, *, coroutine: bool) -> None:
        if not isinstance(function, FunctionType) or not _is_free_function(function):
            raise ProtocolDefinitionError(
                f"RPC {kind} must decorate a free function, got {function!r}"
            )
        if coroutine and not inspect.iscoroutinefunction(function):
            raise ProtocolDefinitionError(
                f"RPC method {function.__qualname__} must be async"
            )

    def ensure_mutable(self) -> None:
        if self._protocol is not None:
            raise ProtocolDefinitionError(
                f"RpcChannel {self.name!r} is frozen because its protocol was "
                "already materialized (directly or by RpcDefinition)"
            )


def join_rpc_name(*parts: str) -> str:
    return ".".join(part for part in parts if part)


def normalize_namespace(value: object) -> str:
    namespace = str(value)
    if namespace:
        _name(namespace, "namespace")
    return namespace


def request_name(wire_name: str) -> str:
    return "".join(part.capitalize() for part in wire_name.split(".")) + "Request"


def _name(value: object, kind: str) -> str:
    """Validate a name relative to a namespace; dots separate its segments."""
    if not isinstance(value, str) or any(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", segment) is None
        for segment in value.split(".")
    ):
        raise ProtocolDefinitionError(f"Invalid RPC {kind}: {value!r}")
    return value


def _channel_names(channel: RpcChannel) -> set[str]:
    return channel._names.union(*(_channel_names(child) for child in channel.children))


def _channel_controls(channel: RpcChannel) -> set[str]:
    return {
        f"{stream.name}.{suffix}"
        for stream in channel.subscriptions
        for suffix in ("open", "cancel")
    }.union(*(_channel_controls(child) for child in channel.children))


def _is_free_function(function: FunctionType) -> bool:
    return "." not in function.__qualname__.rsplit("<locals>.", 1)[-1]


def _docstring_summary(function: Any) -> str | None:
    doc = inspect.getdoc(function)
    return None if not doc else doc.splitlines()[0].strip() or None
