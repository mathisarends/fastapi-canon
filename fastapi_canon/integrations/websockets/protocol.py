from __future__ import annotations

import inspect
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from itertools import chain
from types import FunctionType, UnionType
from typing import (
    Annotated,
    Any,
    NamedTuple,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from fastapi import params as fastapi_params
from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    ConfigDict,
    PydanticSchemaGenerationError,
    TypeAdapter,
    create_model,
)
from pydantic.fields import FieldInfo

from fastapi_canon.integrations.websockets.dependencies import (
    RpcInjectedParameter,
    RpcResolverScope,
    injected_parameter,
)
from fastapi_canon.integrations.websockets.errors import (
    ProtocolDefinitionError,
    RpcError,
    RpcErrorBinding,
    RpcMethodNotFoundError,
    bind_contracts,
    contract_of,
)
from fastapi_canon.integrations.websockets.socket_messages import SocketMessages
from fastapi_canon.integrations.websockets.sockets import (
    RpcMessageType,
    RpcSocket,
    RpcSocketProtocol,
)
from fastapi_canon.integrations.websockets.streams import (
    RpcBinaryInput,
    RpcBinaryOutput,
    RpcStreamDirection,
)


class ChannelPathParams(BaseModel):
    """Signature-derived path fields resolved against the mounted WebSocket route."""

    @classmethod
    def missing_path_inputs(
        cls, available: Iterable[str], *, local: bool = False
    ) -> set[str]:
        supplied = set(available)
        missing: set[str] = set()
        for name, field in cls.model_fields.items():
            # Explicit Path() values can come from a later router inclusion prefix.
            if local and _is_path_parameter(cls.__annotations__.get(name)):
                continue
            inputs = path_input_names(name, field)
            if supplied.isdisjoint(inputs):
                missing.update(inputs)
        return missing


@dataclass(frozen=True, slots=True)
class RpcMethodDefinition:
    name: str
    handler_name: str
    request_name: str
    params: type[BaseModel] | None
    result: Any
    summary: str | None = None
    raises: tuple[type[RpcError], ...] = ()
    server: str | None = None
    function: FunctionType | None = None
    injected_parameters: tuple[RpcInjectedParameter, ...] = ()
    resolver_scope: RpcResolverScope | None = None
    params_parameter: str | None = None
    params_origin: type[BaseModel] | None = None
    timeout_seconds: float | None = None
    _has_timeout: bool = False
    idempotent: bool = False
    params_fields: tuple[str, ...] = ()
    path_parameters: tuple[str, ...] = ()
    path_model: type[BaseModel] | None = None
    params_default: Callable[[], Any] | None = None

    def __post_init__(self) -> None:
        if type(self.idempotent) is not bool:
            raise ProtocolDefinitionError("Method idempotent must be a bool")

    @property
    def has_timeout(self) -> bool:
        """Whether the declaration overrides the inherited request timeout."""
        return self._has_timeout


@dataclass(frozen=True, slots=True)
class RpcNotificationDefinition:
    name: str
    payload: Any
    summary: str | None = None
    server: str | None = None
    function: FunctionType | None = None
    injected_parameters: tuple[RpcInjectedParameter, ...] = ()
    on_error: str = "continue"
    raises: tuple[type[RpcError], ...] = ()


@dataclass(frozen=True, slots=True)
class RpcSubscriptionDefinition:
    name: str
    params: type[BaseModel] | None
    payload: Any
    function: FunctionType
    injected_parameters: tuple[RpcInjectedParameter, ...]
    params_parameter: str | None
    params_origin: type[BaseModel] | None
    resolver_scope: RpcResolverScope
    summary: str | None = None
    server: str | None = None
    raises: tuple[type[RpcError], ...] = ()
    params_fields: tuple[str, ...] = ()
    path_parameters: tuple[str, ...] = ()
    path_model: type[BaseModel] | None = None
    params_default: Callable[[], Any] | None = None


@dataclass(frozen=True, slots=True)
class RpcStreamDefinition:
    name: str
    function: FunctionType
    content_type: str
    summary: str | None
    injected_parameters: tuple[RpcInjectedParameter, ...]
    resolver_scope: RpcResolverScope
    server: str | None = None
    direction: RpcStreamDirection = RpcStreamDirection.SERVER_TO_CLIENT
    input_content_type: str | None = None
    path_parameters: tuple[str, ...] = ()
    path_model: type[BaseModel] | None = None

    @property
    def has_input(self) -> bool:
        return self.direction is not RpcStreamDirection.SERVER_TO_CLIENT

    @property
    def is_generator(self) -> bool:
        return inspect.isasyncgenfunction(self.function)


@dataclass(frozen=True, slots=True)
class RpcSocketDefinition:
    name: str
    function: FunctionType
    messages: tuple[RpcMessageType, ...]
    summary: str | None
    injected_parameters: tuple[RpcInjectedParameter, ...]
    resolver_scope: RpcResolverScope
    closes: tuple[RpcErrorBinding[Any], ...] = ()
    server: str | None = None
    path_parameters: tuple[str, ...] = ()
    path_model: type[BaseModel] | None = None
    protocol: RpcSocketProtocol | None = None
    message_contract: SocketMessages | None = None
    message_parameter: str | None = None


@dataclass(frozen=True, slots=True)
class RpcNotificationTypeDefinition:
    name: str
    payload: type[BaseModel]


class RpcProtocol:
    """The immutable protocol materialized by an ``RpcChannel``."""

    def __init__(
        self,
        *,
        methods: Iterable[RpcMethodDefinition] = (),
        notifications: Iterable[RpcNotificationDefinition] = (),
        subscriptions: Iterable[RpcSubscriptionDefinition] = (),
        notification_types: Iterable[RpcNotificationTypeDefinition] = (),
        streams: Iterable[RpcStreamDefinition] = (),
        sockets: Iterable[RpcSocketDefinition] = (),
        version: int = 1,
    ) -> None:
        self._version = version
        self._methods = _unique(
            "method",
            ((method.name, method) for method in methods),
        )
        self._notifications = _unique(
            "notification",
            ((notification.name, notification) for notification in notifications),
        )
        self._subscriptions = _unique(
            "subscription", ((item.name, item) for item in subscriptions)
        )
        self._notification_types = _unique(
            "notification type",
            ((item.name, item) for item in notification_types),
        )
        self._streams = _unique("stream", ((item.name, item) for item in streams))
        self._sockets = _unique("socket", ((item.name, item) for item in sockets))
        operation_names = (
            set(self._methods) | set(self._notifications) | set(self._subscriptions)
        )
        for stream in self._subscriptions.values():
            for suffix in ("open", "cancel"):
                control = f"{stream.name}.{suffix}"
                if control in operation_names:
                    raise ProtocolDefinitionError(
                        f"RPC stream control collides with an operation: {control}"
                    )
        for operation in chain[
            RpcMethodDefinition | RpcNotificationDefinition | RpcSubscriptionDefinition
        ](self.methods, self.notifications, self.subscriptions):
            bind_contracts(
                (
                    contract
                    for error in operation.raises
                    if (contract := contract_of(error)) is not None
                ),
                owner=f"RPC operation {operation.name}",
            )

    @property
    def version(self) -> int:
        return self._version

    @property
    def methods(self) -> tuple[RpcMethodDefinition, ...]:
        return tuple(self._methods.values())

    @property
    def notifications(self) -> tuple[RpcNotificationDefinition, ...]:
        return tuple(self._notifications.values())

    @property
    def subscriptions(self) -> tuple[RpcSubscriptionDefinition, ...]:
        return tuple(self._subscriptions.values())

    @property
    def notification_types(self) -> tuple[RpcNotificationTypeDefinition, ...]:
        return tuple(self._notification_types.values())

    @property
    def streams(self) -> tuple[RpcStreamDefinition, ...]:
        return tuple(self._streams.values())

    @property
    def sockets(self) -> tuple[RpcSocketDefinition, ...]:
        return tuple(self._sockets.values())

    def method(self, name: str) -> RpcMethodDefinition:
        try:
            return self._methods[name]
        except KeyError as error:
            raise RpcMethodNotFoundError(name) from error


def path_input_names(name: str, field: FieldInfo) -> tuple[str, ...]:
    """Return the route keys Pydantic reads for a path field."""
    match field.validation_alias:
        case AliasChoices() as choices:
            paths = choices.convert_to_aliases()
        case AliasPath() as path:
            paths = [path.path]
        case str() as alias:
            return (alias,)
        case None:
            return (name,)
    flat = tuple(
        path[0] for path in paths if len(path) == 1 and isinstance(path[0], str)
    )
    if not flat:
        raise ProtocolDefinitionError(
            f"URL path parameter {name!r} needs a flat validation alias"
        )
    return flat


def method_definition(
    *,
    name: str,
    function: Any,
    handler_name: str,
    summary: str | None,
    raises: tuple[type[RpcError], ...],
    server: str | None,
    resolver_scope: RpcResolverScope,
    timeout_seconds: float | None = None,
    has_timeout: bool = False,
    idempotent: bool = False,
    request_name: str | None = None,
    params_name: str | None = None,
) -> RpcMethodDefinition:
    request_name = request_name or f"{_pascal_case(handler_name)}Request"
    params_name = params_name if params_name is not None else name
    signature = _router_params_model(function, params_name, "method")
    params_parameter = signature.parameter
    return RpcMethodDefinition(
        timeout_seconds=timeout_seconds,
        _has_timeout=has_timeout,
        idempotent=idempotent,
        name=name,
        handler_name=handler_name,
        request_name=request_name,
        params=signature.model,
        result=_result_annotation(function),
        summary=summary,
        raises=raises,
        server=server,
        function=function,
        injected_parameters=signature.injected,
        resolver_scope=resolver_scope,
        params_parameter=params_parameter,
        params_origin=(
            get_type_hints(function, include_extras=True)[params_parameter]
            if params_parameter is not None
            else None
        ),
        params_fields=signature.fields,
        params_default=signature.default,
        path_parameters=signature.path_parameters,
        path_model=signature.path_model,
    )


def notification_type_definitions(
    annotation: Any,
) -> tuple[RpcNotificationTypeDefinition, ...]:
    definitions: list[RpcNotificationTypeDefinition] = []
    messages = _notification_message_types(annotation)
    for message in messages:
        name = _declared_notification_type(message)
        if not isinstance(name, str):
            if len(messages) == 1:
                continue
            raise ProtocolDefinitionError(
                f"RPC notification type {message.__name__} needs a type field "
                "pinned to a string literal when used in a union"
            )
        definitions.append(RpcNotificationTypeDefinition(name, message))
    return tuple(definitions)


def notification_definition(
    *,
    name: str,
    payload: Any | None,
    function: FunctionType,
    summary: str | None,
    server: str | None,
    on_error: str = "continue",
    raises: tuple[type[RpcError], ...] = (),
) -> RpcNotificationDefinition:
    if not inspect.isasyncgenfunction(function):
        raise ProtocolDefinitionError(
            f"RPC event source {function.__qualname__} must be an async generator"
        )
    hints = get_type_hints(function, include_extras=True)
    result = hints.get("return")
    if result is None:
        raise ProtocolDefinitionError(
            f"RPC event source {function.__qualname__} needs a return annotation"
        )
    origin = get_origin(result)
    if origin not in (AsyncIterator, AsyncGenerator):
        raise ProtocolDefinitionError(
            f"RPC event source {function.__qualname__} must return "
            "AsyncIterator[Payload]"
        )
    yielded = get_args(result)[0]
    if payload is None:
        payload = yielded
    if yielded != payload:
        raise ProtocolDefinitionError(
            f"RPC event source {function.__qualname__} yields {yielded!r}, "
            f"expected {payload!r}"
        )
    signature = _router_params_model(function, name, "event source")
    if signature.model is not None or signature.path_model is not None:
        raise ProtocolDefinitionError(
            f"RPC event source {function.__qualname__} parameters must use Inject[T]"
        )
    return RpcNotificationDefinition(
        name=name,
        payload=payload,
        summary=summary,
        server=server,
        function=function,
        injected_parameters=signature.injected,
        on_error=on_error,
        raises=raises,
    )


def subscription_definition(
    *,
    name: str,
    function: FunctionType,
    summary: str | None,
    resolver_scope: RpcResolverScope,
    raises: tuple[type[RpcError], ...] = (),
) -> RpcSubscriptionDefinition:
    if not inspect.isasyncgenfunction(function):
        raise ProtocolDefinitionError(
            f"RPC message stream {function.__qualname__} must be an async generator"
        )
    hints = get_type_hints(function, include_extras=True)
    payload = stream_payload(function)
    TypeAdapter(payload).json_schema()
    signature = _router_params_model(function, name, "message stream")
    params_parameter = signature.parameter
    return RpcSubscriptionDefinition(
        name=name,
        params=signature.model,
        payload=payload,
        function=function,
        injected_parameters=signature.injected,
        params_parameter=params_parameter,
        params_origin=(
            hints[params_parameter] if params_parameter is not None else None
        ),
        resolver_scope=resolver_scope,
        summary=summary,
        raises=raises,
        params_fields=signature.fields,
        params_default=signature.default,
        path_parameters=signature.path_parameters,
        path_model=signature.path_model,
    )


def stream_definition(
    *,
    name: str,
    function: FunctionType,
    content_type: str,
    summary: str | None,
    resolver_scope: RpcResolverScope,
    input_content_type: str | None = None,
) -> RpcStreamDefinition:
    qualname = function.__qualname__
    hints, injected, path_parameters = _split_parameters(function)
    counts = Counter(parameter.dependency for parameter in injected)
    for dependency in (RpcBinaryInput, RpcBinaryOutput):
        if counts[dependency] > 1:
            raise ProtocolDefinitionError(
                f"RPC binary stream {qualname} injects {dependency.__name__} "
                "more than once"
            )
    has_input = RpcBinaryInput in counts
    has_output = RpcBinaryOutput in counts
    result = hints.get("return")
    if inspect.isasyncgenfunction(function):
        if stream_payload(function) is not bytes:
            raise ProtocolDefinitionError(
                f"RPC binary stream {qualname} must yield bytes and be annotated "
                "as AsyncIterator[bytes]"
            )
        if has_input or has_output:
            raise ProtocolDefinitionError(
                f"RPC binary stream {qualname} is an async generator and cannot "
                "inject RpcBinaryInput or RpcBinaryOutput; write an async "
                "function that injects them and calls `await output.send(frame)` "
                "instead of yielding"
            )
        direction = RpcStreamDirection.SERVER_TO_CLIENT
    elif inspect.iscoroutinefunction(function):
        if not (has_input or has_output):
            raise ProtocolDefinitionError(
                f"RPC binary stream {qualname} must be an async generator "
                "yielding bytes or an async function that injects "
                "RpcBinaryInput and/or RpcBinaryOutput"
            )
        if result is not None and result is not type(None):
            raise ProtocolDefinitionError(
                f"RPC binary stream {qualname} must return None; send output "
                "frames through RpcBinaryOutput"
            )
        if has_input and has_output:
            direction = RpcStreamDirection.BIDIRECTIONAL
        elif has_input:
            direction = RpcStreamDirection.CLIENT_TO_SERVER
        else:
            direction = RpcStreamDirection.SERVER_TO_CLIENT
    else:
        raise ProtocolDefinitionError(
            f"RPC binary stream {qualname} must be an async generator or an "
            "async function"
        )
    if input_content_type is not None and not has_input:
        raise ProtocolDefinitionError(
            f"RPC binary stream {qualname} declares input_content_type but does "
            "not inject RpcBinaryInput"
        )
    if not has_input:
        resolved_input_content_type = None
    elif input_content_type is not None:
        resolved_input_content_type = input_content_type
    else:
        resolved_input_content_type = content_type
    return RpcStreamDefinition(
        name=name,
        function=function,
        content_type=content_type,
        summary=summary,
        injected_parameters=injected,
        resolver_scope=resolver_scope,
        direction=direction,
        input_content_type=resolved_input_content_type,
        path_parameters=tuple(parameter.name for parameter in path_parameters),
        path_model=_path_model(function, hints, path_parameters),
    )


def stream_payload(function: FunctionType) -> Any:
    annotation = get_type_hints(function, include_extras=True).get("return")
    annotation = _stream_annotation(annotation)
    if get_origin(annotation) not in (AsyncIterator, AsyncGenerator) or not get_args(
        annotation
    ):
        raise ProtocolDefinitionError("RPC stream must return AsyncIterator[Payload]")
    payload = _resolve_alias(get_args(annotation)[0])
    normalized = _stream_annotation(payload)
    if normalized is Any or (
        get_origin(normalized) in (Union, UnionType)
        and any(
            _stream_annotation(item) in (bytes, Any) for item in get_args(normalized)
        )
    ):
        raise ProtocolDefinitionError(
            "Stream payload must unambiguously declare bytes or serializable messages"
        )
    return bytes if normalized is bytes else payload


def socket_definition(
    *,
    name: str,
    function: FunctionType,
    messages: tuple[RpcMessageType, ...],
    protocol: RpcSocketProtocol | None,
    closes: tuple[RpcErrorBinding[Any], ...],
    summary: str | None,
    resolver_scope: RpcResolverScope,
) -> RpcSocketDefinition:
    qualname = function.__qualname__
    if not inspect.iscoroutinefunction(function):
        raise ProtocolDefinitionError(
            f"RPC socket {qualname} must be an async function"
        )
    hints, injected, path_parameters = _split_parameters(function)
    socket_parameters = [
        (p, _resolve_alias(p.dependency))
        for p in injected
        if _resolve_alias(p.dependency) is RpcSocket
        or get_origin(_resolve_alias(p.dependency)) is RpcSocket
    ]
    if len(socket_parameters) > 1 or (not socket_parameters and not path_parameters):
        raise ProtocolDefinitionError(
            f"RPC socket {qualname} must inject RpcSocket exactly once "
            "or accept a typed message parameter"
        )
    message_contract = None
    message_parameter = None
    payload_types = None
    result = hints.get("return")
    if not socket_parameters:
        parameter, *path_parameters = path_parameters
        if (
            parameter.kind
            not in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
            or parameter.default is not inspect.Parameter.empty
        ):
            raise ProtocolDefinitionError(
                "Socket message parameters must be required and passable by name"
            )
        incoming = hints[parameter.name]
        if incoming is Any or result is Any or result is None:
            raise ProtocolDefinitionError(
                "Socket message handlers need concrete message and return annotations"
            )
        payload_types = (incoming, result)
        message_parameter = parameter.name
    else:
        socket_parameter, annotation = socket_parameters[0]
        arguments = get_args(annotation)
        if arguments:
            if len(arguments) != 2:
                raise ProtocolDefinitionError(
                    "Typed sockets must inject RpcSocket[Incoming, Outgoing]"
                )
            payload_types = arguments
        injected = tuple(
            RpcInjectedParameter(p.name, RpcSocket) if p is socket_parameter else p
            for p in injected
        )
        if result is not None and result is not type(None):
            raise ProtocolDefinitionError(
                f"RPC socket {qualname} must return None; "
                "send messages through RpcSocket"
            )
    if payload_types is not None:
        incoming, outgoing = payload_types
        if _resolve_alias(incoming) is Any or _resolve_alias(outgoing) is Any:
            raise ProtocolDefinitionError(
                "Typed sockets need concrete incoming and outgoing annotations"
            )
        if RpcMessageType.TEXT not in messages:
            raise ProtocolDefinitionError("Typed sockets require text messages")
        if protocol is not None and protocol.encoding != "json":
            raise ProtocolDefinitionError("Typed sockets require JSON encoding")
        try:
            message_contract = SocketMessages.create(incoming, outgoing)
            message_contract.client.json_schema()
            message_contract.server.json_schema(mode="serialization")
        except (PydanticSchemaGenerationError, TypeError, ValueError) as error:
            raise ProtocolDefinitionError(
                "Invalid typed socket message types"
            ) from error
        messages = (RpcMessageType.TEXT,)
    return RpcSocketDefinition(
        name=name,
        function=function,
        messages=messages,
        summary=summary,
        injected_parameters=injected,
        resolver_scope=resolver_scope,
        closes=closes,
        path_parameters=tuple(parameter.name for parameter in path_parameters),
        path_model=_path_model(function, hints, path_parameters, kind="socket"),
        protocol=protocol,
        message_contract=message_contract,
        message_parameter=message_parameter,
    )


def _router_params_model(function: Any, name: str, kind: str) -> _ParamsModel:
    """Read a handler's JSON-RPC params, path values and injected dependencies.

    A single positional Pydantic model is the complete params object. Other
    named parameters form one generated params model, by name. Parameters
    annotated with FastAPI's ``Path()`` read the connection's path values.
    """
    hints, injected, parameters = _split_parameters(function)
    path_parameters = [p for p in parameters if _is_path_parameter(hints[p.name])]
    wire_parameters = [p for p in parameters if p not in path_parameters]
    path_model = (
        _path_model(function, hints, path_parameters, kind=f"RPC {kind}")
        if path_parameters
        else None
    )
    path_names = tuple(parameter.name for parameter in path_parameters)
    if not wire_parameters:
        return _ParamsModel(None, None, (), injected, path_names, path_model)
    if len(wire_parameters) == 1:
        parameter = wire_parameters[0]
        annotation = hints[parameter.name]
        if parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD and _is_model(
            annotation
        ):
            return _ParamsModel(
                annotation,
                parameter.name,
                (),
                injected,
                path_names,
                path_model,
                None
                if parameter.default is inspect.Parameter.empty
                else partial(deepcopy, parameter.default),
            )
    fields: dict[str, Any] = {}
    for parameter in wire_parameters:
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise ProtocolDefinitionError(
                f"RPC {kind} {function.__qualname__} parameter {parameter.name!r} "
                "must be passable by name"
            )
        default = (
            ... if parameter.default is inspect.Parameter.empty else parameter.default
        )
        fields[parameter.name] = (hints[parameter.name], default)
    try:
        model = create_model(
            f"{_pascal_case(name.replace('.', '_'))}Params",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )
    except (PydanticSchemaGenerationError, NameError, TypeError) as error:
        raise ProtocolDefinitionError(
            f"RPC {kind} {function.__qualname__} has unsupported params: {error}"
        ) from error
    return _ParamsModel(model, None, tuple(fields), injected, path_names, path_model)


def _is_path_parameter(annotation: Any) -> bool:
    annotation = _resolve_alias(annotation)
    return get_origin(annotation) is Annotated and any(
        isinstance(item, fastapi_params.Path) for item in annotation.__metadata__
    )


def _split_parameters(
    function: Any,
) -> _SplitParameters:
    parameters = tuple(inspect.signature(function).parameters.values())
    hints = get_type_hints(function, include_extras=True)
    injected: list[RpcInjectedParameter] = []
    wire_parameters: list[inspect.Parameter] = []
    for parameter in parameters:
        annotation = hints.get(parameter.name)
        if annotation is None:
            raise ProtocolDefinitionError(
                f"RPC handler {function.__qualname__} parameter "
                f"{parameter.name!r} needs an annotation"
            )
        try:
            dependency = injected_parameter(parameter.name, annotation)
        except TypeError as error:
            raise ProtocolDefinitionError(str(error)) from error
        if dependency is None:
            wire_parameters.append(parameter)
            continue
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise ProtocolDefinitionError(
                f"Injected RPC parameter {parameter.name!r} must be passable by name"
            )
        if parameter.default is not inspect.Parameter.empty:
            raise ProtocolDefinitionError(
                f"Injected RPC parameter {parameter.name!r} cannot have a default"
            )
        injected.append(dependency)
    return _SplitParameters(hints, tuple(injected), wire_parameters)


def _result_annotation(function: Any) -> Any:
    result = get_type_hints(function, include_extras=True).get("return")
    if result is None:
        raise ProtocolDefinitionError(
            f"RPC handler {function.__qualname__} needs a return annotation"
        )
    return result


def _stream_annotation(annotation: Any) -> Any:
    while True:
        if isinstance(annotation, TypeAliasType):
            annotation = annotation.__value__
        elif get_origin(annotation) is Annotated:
            annotation = get_args(annotation)[0]
        else:
            return annotation


def _resolve_alias(annotation: Any) -> Any:
    while isinstance(annotation, TypeAliasType):
        annotation = annotation.__value__
    return annotation


def _path_model(
    function: Any,
    hints: dict[str, Any],
    parameters: list[inspect.Parameter],
    *,
    kind: str = "binary stream",
) -> type[BaseModel] | None:
    if not parameters:
        return None
    fields: dict[str, Any] = {}
    for parameter in parameters:
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise ProtocolDefinitionError(
                f"RPC {kind} path parameter {parameter.name!r} must be passable by name"
            )
        annotation = hints[parameter.name]
        if _is_model(annotation):
            raise ProtocolDefinitionError(_path_type_message(parameter.name, kind))
        default = (
            ... if parameter.default is inspect.Parameter.empty else parameter.default
        )
        fields[parameter.name] = (annotation, default)
    try:
        return create_model(
            f"{_pascal_case(function.__name__)}PathParams",
            __base__=ChannelPathParams,
            **fields,
        )
    except PydanticSchemaGenerationError as error:
        names = ", ".join(fields)
        raise ProtocolDefinitionError(_path_type_message(names, kind)) from error


def _path_type_message(name: str, kind: str) -> str:
    kind = kind.removeprefix("RPC ")
    return (
        f"RPC {kind} parameter {name!r} is not a path variable type; "
        "path variables are scalars such as str, int, UUID, or an Enum, and "
        "dependencies use Inject[T]"
    )


def _notification_message_types(annotation: Any) -> tuple[type[BaseModel], ...]:
    value = (
        annotation.__value__ if isinstance(annotation, TypeAliasType) else annotation
    )
    if get_origin(value) is Annotated:
        value = get_args(value)[0]
    members = get_args(value) if get_origin(value) in (Union, UnionType) else (value,)
    if not all(_is_model(member) for member in members):
        raise ProtocolDefinitionError("RPC event payload must contain Pydantic models")
    return members


def _declared_notification_type(message: type[BaseModel]) -> Any:
    schema = message.model_json_schema(mode="serialization", by_alias=True)
    return schema.get("properties", {}).get("type", {}).get("const")


def _unique[ValueT](
    kind: str,
    entries: Iterable[tuple[str, ValueT]],
) -> dict[str, ValueT]:
    unique: dict[str, ValueT] = {}
    for name, value in entries:
        if name in unique:
            raise ProtocolDefinitionError(f"Duplicate RPC {kind}: {name}")
        unique[name] = value
    return unique


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _pascal_case(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_"))


class _ParamsModel(NamedTuple):
    model: type[BaseModel] | None
    parameter: str | None
    fields: tuple[str, ...]
    injected: tuple[RpcInjectedParameter, ...]
    path_parameters: tuple[str, ...]
    path_model: type[BaseModel] | None
    default: Callable[[], Any] | None = None


class _SplitParameters(NamedTuple):
    hints: dict[str, Any]
    injected: tuple[RpcInjectedParameter, ...]
    wire: list[inspect.Parameter]
