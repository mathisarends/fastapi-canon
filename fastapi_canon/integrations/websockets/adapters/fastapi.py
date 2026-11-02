import asyncio
import inspect
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, nullcontext, suppress
from types import NoneType
from typing import (
    Annotated,
    Any,
    Protocol,
    get_args,
    get_origin,
    get_type_hints,
    overload,
    runtime_checkable,
)

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.websockets import WebSocketState
from pydantic import BaseModel
from starlette.types import Message

from fastapi_canon.error.catalog import registry_scope
from fastapi_canon.error.contracts import INSTALLED_REGISTRY_STATE_KEY
from fastapi_canon.integrations.websockets.activity import notify_activity
from fastapi_canon.integrations.websockets.channel import RpcChannel
from fastapi_canon.integrations.websockets.connection import (
    REJECTION_CLOSES,
    RpcBeforeAccept,
    RpcConnection,
    RpcConnectionClose,
    RpcDisconnect,
    RpcHandshake,
    RpcLimits,
    RpcRejection,
    RpcRejections,
    RpcWebSocket,
    chain_rejections,
)
from fastapi_canon.integrations.websockets.contract import RpcContract, ServerVariable
from fastapi_canon.integrations.websockets.definition import (
    RpcAnyEndpoint,
    RpcDefinition,
    RpcEndpoint,
    RpcSocketEndpoint,
    RpcStreamEndpoint,
    endpoint_rejections,
)
from fastapi_canon.integrations.websockets.dependencies import (
    RpcInjectedParameter,
    RpcResolverLike,
    as_resolver,
    connection_scope,
    injected_parameter,
)
from fastapi_canon.integrations.websockets.drain import RpcDrain
from fastapi_canon.integrations.websockets.errors import (
    RpcErrorBinding,
    contract_rejections,
    rejecting_contracts,
)
from fastapi_canon.integrations.websockets.middleware import (
    RpcMiddlewareLike,
    _current_serving,
    _record_error,
    _record_rejection,
    _serving_scope,
    _ServingState,
)
from fastapi_canon.integrations.websockets.protocol import RpcProtocol
from fastapi_canon.integrations.websockets.runtime import _rejection
from fastapi_canon.integrations.websockets.server import RpcErrorMapper
from fastapi_canon.integrations.websockets.websocket import (
    CLOSE_CODES,
    REJECTION_CLOSE_CODES,
    close_reason,
)

type FastApiResolverFactory = Callable[[WebSocket], RpcResolverLike]

_HTTP_STATUS = {
    RpcRejection.UNAUTHORIZED: 401,
    RpcRejection.FORBIDDEN: 403,
    RpcRejection.NOT_FOUND: 404,
    RpcRejection.PROTOCOL_ERROR: 400,
    RpcRejection.UNAVAILABLE: 503,
    RpcRejection.INTERNAL_ERROR: 500,
}

_OBSERVED_SOCKET_KEY = "fastapi_canon.integrations.websockets.observed_socket"


class FastApiSocket:
    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        client = websocket.client
        self._handshake = RpcHandshake(
            path=websocket.url.path,
            headers=dict(websocket.headers),
            query_params=dict(websocket.query_params),
            path_params=dict(websocket.path_params),
            subprotocols=tuple(websocket.scope.get("subprotocols", ())),
            client=None if client is None else (client.host, client.port),
        )

    @property
    def handshake(self) -> RpcHandshake:
        return self._handshake

    async def accept(self, subprotocol: str | None = None) -> None:
        await self._websocket.accept(subprotocol=subprotocol)

    async def reject(
        self,
        rejection: RpcRejection,
        reason: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        await _reject(self._websocket, rejection, reason, headers=headers)

    async def receive(self) -> str | bytes:
        message = await self._websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise RpcDisconnect(
                message.get("code") or 1000, str(message.get("reason", ""))
            )
        value = message.get("text")
        return value if value is not None else message.get("bytes", b"")

    async def send(self, message: str) -> None:
        try:
            await self._websocket.send_text(message)
        except WebSocketDisconnect as error:
            raise RpcDisconnect(error.code, error.reason or "") from error

    async def send_bytes(self, data: bytes) -> None:
        try:
            await self._websocket.send_bytes(data)
        except WebSocketDisconnect as error:
            raise RpcDisconnect(error.code, error.reason or "") from error

    async def close(self, close: RpcConnectionClose, reason: str) -> None:
        await self._websocket.close(CLOSE_CODES[close], close_reason(reason))


async def _reject(
    websocket: WebSocket,
    rejection: RpcRejection,
    reason: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> None:
    if "websocket.http.response" in websocket.scope.get("extensions", {}):
        await websocket.send_denial_response(
            Response(
                reason,
                status_code=_HTTP_STATUS[rejection],
                media_type="text/plain",
                headers=dict(headers or {}),
            )
        )
    else:
        await websocket.close(REJECTION_CLOSE_CODES[rejection], close_reason(reason))


async def serve_websocket(
    endpoint: RpcAnyEndpoint,
    websocket: WebSocket,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    error_mapper: RpcErrorMapper | None = None,
    limits: RpcLimits | None = None,
    before_accept: RpcBeforeAccept | None = None,
    rejections: RpcRejections | None = None,
    middleware: Sequence[RpcMiddlewareLike] = (),
) -> None:
    active = websocket.scope.get("fastapi_canon.integrations.websockets.serving")
    socket = (
        active.socket
        if isinstance(active, _ServingState) and active.scope.endpoint is endpoint
        else FastApiSocket(websocket)
    )

    async def serve() -> None:
        if isinstance(endpoint, RpcEndpoint):
            await endpoint.serve(
                socket,
                resolver=resolver,
                context=context,
                error_mapper=error_mapper,
                limits=limits,
                before_accept=before_accept,
                rejections=rejections,
                middleware=middleware,
            )
        elif isinstance(endpoint, RpcStreamEndpoint):
            await endpoint.serve(
                socket,
                resolver=resolver,
                context=context,
                limits=limits,
                before_accept=before_accept,
                error_mapper=error_mapper,
                rejections=rejections,
                middleware=middleware,
            )
        else:
            await endpoint.serve(
                socket,
                resolver=resolver,
                context=context,
                limits=limits,
                before_accept=before_accept,
                rejections=rejections,
                middleware=middleware,
            )

    state = getattr(websocket.scope.get("app"), "state", None)
    with registry_scope(getattr(state, INSTALLED_REGISTRY_STATE_KEY, None)):
        serving = asyncio.create_task(serve())
    task = asyncio.current_task()
    cancelling = task.cancelling() if task is not None else 0
    try:
        # Preserve the caller's cancellation instead of a child's gather error.
        await asyncio.shield(serving)
    except asyncio.CancelledError as cancellation:
        if task is None or task.cancelling() == cancelling:
            # Service drain can cancel the endpoint independently of ASGI.
            return
        serving.cancel()
        while not serving.done():
            with suppress(asyncio.CancelledError):
                await asyncio.shield(serving)
        if not serving.cancelled():
            serving.result()
        raise cancellation


@runtime_checkable
class FastApiResolver(Protocol):
    """Resolve RPC dependencies per WebSocket and prepare the context function.

    Pass ``Dishka`` from ``fastapi_canon.integrations.websockets.adapters.dishka``
    as ``resolver=`` to ``RpcRouter`` to integrate Dishka.
    """

    def for_websocket(self, websocket: WebSocket) -> RpcResolverLike: ...

    def dependency[FunctionT: Callable[..., Any]](
        self, function: FunctionT
    ) -> FunctionT: ...


class RpcRouter(APIRouter):
    """Define WebSocket endpoints, mount their routes and export one contract."""

    def __init__(
        self,
        path: str | None = None,
        *,
        channels: Sequence[RpcChannel] | None = None,
        version: int = 1,
        context: Callable[..., Awaitable[Any]] | None = None,
        resolver: RpcResolverLike | FastApiResolver | None = None,
        rejects: Sequence[RpcErrorBinding[Any]] = (),
        rejections: RpcRejections | None = None,
        middleware: Sequence[RpcMiddlewareLike] = (),
        error_mapper: RpcErrorMapper | None = None,
        limits: RpcLimits | None = None,
        strict_errors: bool = False,
        drain: RpcDrain | None = None,
        prefix: str = "",
        before_accept: RpcBeforeAccept | None = None,
    ) -> None:
        super().__init__(prefix=prefix)
        if (path is None) != (channels is None):
            raise ValueError("RpcRouter needs path and channels together")
        self._definition = RpcDefinition(
            version=version,
            error_mapper=error_mapper,
            limits=limits,
            middleware=middleware,
            strict_errors=strict_errors,
            drain=drain,
            rejects=rejects,
        )
        self._resolver: RpcResolverLike | None = None
        self._websocket_resolver: FastApiResolver | None = None
        if isinstance(resolver, FastApiResolver):
            self._websocket_resolver = resolver
        else:
            self._resolver = resolver
        self._context_type = None if context is None else _context_type(context)
        self._context = context
        if context is not None and self._websocket_resolver is not None:
            self._context = self._websocket_resolver.dependency(context)
        self._rejections = chain_rejections(
            rejections, contract_rejections(rejecting_contracts(rejects, "RpcRouter"))
        )
        self._error_mapper = error_mapper
        self._limits = limits
        self._before_accept = before_accept
        self._middleware = tuple(middleware)
        self._mounted: set[RpcAnyEndpoint] = set()
        if path is not None and channels is not None:
            self.socket(path, channels=channels)
            self._definition.freeze()

    @property
    def drain(self) -> RpcDrain:
        return self._definition.drain

    @property
    def protocol(self) -> RpcProtocol:
        return self._definition.protocol

    @property
    def endpoints(self) -> tuple[RpcAnyEndpoint, ...]:
        return self._definition.endpoints

    def endpoint(self, name: str) -> RpcAnyEndpoint:
        return self._definition.endpoint(name)

    @property
    def version(self) -> int:
        return self._definition.version

    def freeze(self) -> RpcProtocol:
        return self._definition.freeze()

    def match(self, path: str) -> tuple[RpcAnyEndpoint, dict[str, str]] | None:
        return self._definition.match(path)

    async def serve(self, socket: RpcWebSocket, **options: Any) -> None:
        options.setdefault("resolver", self._resolver)
        options.setdefault("rejections", self._rejections)
        await self._definition.serve(socket, **options)

    def contract(
        self,
        *,
        title: str,
        base_url: str,
        description: str = "Typed JSON-RPC API.",
        variables: Mapping[str, ServerVariable] | None = None,
        app: FastAPI | None = None,
    ) -> RpcContract:
        return self._definition.contract(
            title=title,
            base_url=base_url,
            description=description,
            variables=variables,
            app=app,
        )

    @overload
    def socket(
        self,
        path: str,
        /,
        *,
        channels: Sequence[RpcChannel],
        name: str | None = None,
        error_mapper: RpcErrorMapper | None = None,
        middleware: Sequence[RpcMiddlewareLike] = (),
        limits: RpcLimits | None = None,
        subprotocol: str | None = None,
        summary: str | None = None,
        before_accept: RpcBeforeAccept | None = None,
        path_model: type[BaseModel] | None = None,
        context: type[None] = NoneType,
        close_when_events_complete: bool = False,
        rejects: Sequence[RpcErrorBinding[Any]] = (),
        serve: Callable[..., Awaitable[None]] | None = None,
    ) -> RpcEndpoint[None]: ...

    @overload
    def socket[ContextT](
        self,
        path: str,
        /,
        *,
        channels: Sequence[RpcChannel],
        name: str | None = None,
        error_mapper: RpcErrorMapper | None = None,
        middleware: Sequence[RpcMiddlewareLike] = (),
        limits: RpcLimits | None = None,
        subprotocol: str | None = None,
        summary: str | None = None,
        before_accept: RpcBeforeAccept | None = None,
        path_model: type[BaseModel] | None = None,
        context: type[ContextT],
        close_when_events_complete: bool = False,
        rejects: Sequence[RpcErrorBinding[Any]] = (),
        serve: Callable[..., Awaitable[None]] | None = None,
    ) -> RpcEndpoint[ContextT]: ...

    @overload
    def socket[ContextT](
        self,
        path: str,
        handler: Callable[..., Any],
        /,
        *,
        name: str | None = None,
        middleware: Sequence[RpcMiddlewareLike] = (),
        limits: RpcLimits | None = None,
        subprotocol: str | None = None,
        summary: str | None = None,
        before_accept: RpcBeforeAccept | None = None,
        context: type[ContextT] = NoneType,
        rejects: Sequence[RpcErrorBinding[Any]] = (),
        serve: Callable[..., Awaitable[None]] | None = None,
    ) -> RpcSocketEndpoint[ContextT]: ...

    def socket(
        self,
        path: str,
        handler: Callable[..., Any] | None = None,
        /,
        *,
        channels: Sequence[RpcChannel] | None = None,
        context: type[Any] | None = None,
        serve: Callable[..., Awaitable[None]] | None = None,
        **options: Any,
    ) -> RpcEndpoint[Any] | RpcSocketEndpoint[Any]:
        options.setdefault("before_accept", self._before_accept)
        endpoint = self._definition.socket(
            self.prefix + path,
            handler,
            channels=channels,
            context=context or self._context_type or NoneType,
            **options,
        )
        self.mount(endpoint, handler=serve)
        return endpoint

    def stream[ContextT](
        self,
        path: str,
        stream: Callable[..., Any],
        /,
        *,
        name: str | None = None,
        middleware: Sequence[RpcMiddlewareLike] = (),
        limits: RpcLimits | None = None,
        subprotocol: str | None = None,
        summary: str | None = None,
        before_accept: RpcBeforeAccept | None = None,
        error_mapper: RpcErrorMapper | None = None,
        context: type[ContextT] = NoneType,
        rejects: Sequence[RpcErrorBinding[Any]] = (),
        serve: Callable[..., Awaitable[None]] | None = None,
    ) -> RpcStreamEndpoint[ContextT]:
        endpoint = self._definition.stream(
            self.prefix + path,
            stream,
            name=name,
            middleware=middleware,
            limits=limits,
            subprotocol=subprotocol,
            summary=summary,
            before_accept=before_accept or self._before_accept,
            error_mapper=error_mapper,
            context=self._context_type
            if context is NoneType and self._context_type is not None
            else context,
            rejects=rejects,
        )
        self.mount(endpoint, handler=serve)
        return endpoint

    def mount[ContextT](
        self,
        endpoint: (
            RpcEndpoint[ContextT]
            | RpcStreamEndpoint[ContextT]
            | RpcSocketEndpoint[ContextT]
        ),
        *,
        handler: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        """Add a WebSocket route for ``endpoint`` at its declared path."""
        if endpoint in self._mounted:
            raise ValueError(f"RPC endpoint {endpoint.name!r} is already mounted")
        self._check_context(endpoint)
        dependencies = []
        middleware = () if endpoint.service is self._definition else self._middleware
        if middleware or endpoint.middleware:
            dependencies.append(
                Depends(
                    _middleware_dependency(
                        endpoint, middleware, custom=handler is not None
                    )
                )
            )
        dependencies.append(
            Depends(
                _reject_failures(
                    chain_rejections(self._rejections, endpoint_rejections(endpoint))
                )
            )
        )
        self.websocket(
            self._route_path(endpoint),
            name=endpoint.name,
            dependencies=dependencies,
        )(
            self._handler(endpoint)
            if handler is None
            else self._custom_handler(endpoint, handler)
        )
        contributions = list(getattr(self, "_canon_error_registries", ()))

        def collect(channel: Any) -> None:
            if channel.error_registry is not None:
                contributions.append(channel.error_registry)
            for child in channel.children:
                collect(child)

        for channel in endpoint.channels if isinstance(endpoint, RpcEndpoint) else ():
            collect(channel)
        self._canon_error_registries = tuple(contributions)  # type: ignore[attr-defined]
        self._mounted.add(endpoint)

    def _check_context(self, endpoint: RpcAnyEndpoint) -> None:
        declared = endpoint.context
        provided = self._context_type
        if declared is None and provided is None:
            return
        if declared is None or provided is None or not issubclass(provided, declared):
            raise TypeError(
                f"RPC endpoint {endpoint.name!r} declares context "
                f"{_type_name(declared)}, but these routes provide "
                f"{_type_name(provided)}"
            )

    def _route_path(self, endpoint: RpcAnyEndpoint) -> str:
        prefix = self.prefix
        path = endpoint.path
        if not path.startswith(prefix) or path[len(prefix) :][:1] not in ("", "/"):
            raise ValueError(
                f"RPC endpoint path {path!r} is outside the router prefix {prefix!r}"
            )
        return path[len(prefix) :]

    def _handler(self, endpoint: RpcAnyEndpoint) -> Callable[..., Awaitable[None]]:
        context_type = endpoint.context

        async def handler(websocket: WebSocket, context: Any = None) -> None:
            await serve_websocket(
                endpoint,
                websocket,
                resolver=(
                    self._websocket_resolver.for_websocket(websocket)
                    if self._websocket_resolver is not None
                    else self._resolver
                ),
                context=None if context_type is None else {context_type: context},
                error_mapper=None
                if endpoint.service is self._definition
                else self._error_mapper,
                limits=None if endpoint.service is self._definition else self._limits,
                before_accept=None
                if endpoint.service is self._definition
                else self._before_accept,
                rejections=self._rejections,
            )

        parameters = [
            inspect.Parameter(
                "websocket",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=WebSocket,
            )
        ]
        if self._context is not None:
            parameters.append(
                inspect.Parameter(
                    "context",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=Annotated[Any, Depends(self._context)],
                )
            )
        handler.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
        return handler

    def _custom_handler(
        self,
        endpoint: RpcAnyEndpoint,
        function: Callable[..., Awaitable[None]],
    ) -> Callable[..., Awaitable[None]]:
        if not inspect.iscoroutinefunction(function):
            raise TypeError("handler= expects an async function")
        signature = inspect.signature(function)
        hints = get_type_hints(function, include_extras=True)
        injected: list[RpcInjectedParameter] = []
        parameters: list[inspect.Parameter] = []
        websocket_name = None
        for parameter in signature.parameters.values():
            if parameter.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                raise TypeError(
                    f"Custom RPC handler parameter {parameter.name!r} must be "
                    "positional-or-keyword or keyword-only"
                )
            annotation = hints.get(parameter.name, parameter.annotation)
            dependency = injected_parameter(parameter.name, annotation)
            if dependency is not None:
                if (
                    endpoint.context is not None
                    and dependency.dependency is not endpoint.context
                    and issubclass(dependency.dependency, endpoint.context)
                ):
                    raise TypeError(
                        f"Custom RPC handler parameter {parameter.name!r} must "
                        f"inject the declared context type {endpoint.context.__name__}"
                    )
                injected.append(dependency)
                continue
            base_type = (
                get_args(annotation)[0]
                if get_origin(annotation) is Annotated
                else annotation
            )
            if (
                endpoint.context is not None
                and inspect.isclass(base_type)
                and issubclass(base_type, endpoint.context)
            ):
                raise TypeError(
                    f"Custom RPC handler parameter {parameter.name!r} must use "
                    f"Inject[{endpoint.context.__name__}] for the endpoint context"
                )
            if base_type is WebSocket:
                websocket_name = parameter.name
            parameters.append(parameter.replace(annotation=annotation))

        if websocket_name is None:
            websocket_name = _parameter_name(signature, "_canon_websockets_websocket")
            parameters.append(
                inspect.Parameter(
                    websocket_name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=WebSocket,
                )
            )
        context_name = _parameter_name(signature, "_canon_websockets_context")
        if self._context is not None:
            parameters.append(
                inspect.Parameter(
                    context_name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=Annotated[Any, Depends(self._context)],
                )
            )

        async def handler(**arguments: Any) -> None:
            websocket = arguments[websocket_name]
            observe = websocket.scope.get(_OBSERVED_SOCKET_KEY)
            if observe is not None:
                websocket = await observe()
                arguments[websocket_name] = websocket
            if websocket_name not in signature.parameters:
                arguments.pop(websocket_name)
            context = arguments.pop(context_name, None)
            values = None if endpoint.context is None else {endpoint.context: context}
            resolver = as_resolver(
                self._websocket_resolver.for_websocket(websocket)
                if self._websocket_resolver is not None
                else self._resolver
            )
            async with connection_scope(resolver, values) as scoped:
                for parameter in injected:
                    arguments[parameter.name] = await scoped.resolve(
                        parameter.dependency
                    )
                await function(**arguments)

        handler.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
        handler.__annotations__ = {
            parameter.name: parameter.annotation for parameter in parameters
        }
        handler.__annotations__["return"] = type(None)
        return (
            self._websocket_resolver.dependency(handler)
            if self._websocket_resolver is not None
            else handler
        )


def _parameter_name(signature: inspect.Signature, name: str) -> str:
    while name in signature.parameters:
        name = "_" + name
    return name


def _context_type(function: Callable[..., Any]) -> type[Any]:
    if not callable(function) or inspect.isclass(function):
        raise TypeError(f"context= expects an async function, got {function!r}")
    context_type = get_type_hints(function).get("return")
    if context_type in (Any, object, type(None)) or not inspect.isclass(context_type):
        raise TypeError(
            f"context= needs a concrete class as return annotation of "
            f"{getattr(function, '__qualname__', function)!r}, got {context_type!r}"
        )
    return context_type


def _type_name(value: type[Any] | None) -> str:
    return "none" if value is None else value.__name__


def _reject_failures(
    rejections: RpcRejections | None,
) -> Callable[[WebSocket], AsyncGenerator[None, None]]:
    # Runs before context dependencies; connection middleware wraps its cleanup.
    async def reject_failures(websocket: WebSocket) -> AsyncGenerator[None, None]:
        try:
            yield
        except Exception as error:
            state = _current_serving()
            if (
                state is None
                or websocket.scope.get("fastapi_canon.integrations.websockets.serving")
                is not state
            ):
                state = None
            if state is not None:
                _record_error(error)
            rejected = _rejection(error, rejections)
            if rejected is None:
                raise
            observe = websocket.scope.get(_OBSERVED_SOCKET_KEY)
            if observe is not None:
                websocket = await observe()
            if websocket.application_state is WebSocketState.CONNECTING:
                if state is not None:
                    _record_rejection(rejected.rejection)
                await _reject(
                    websocket,
                    rejected.rejection,
                    rejected.reason,
                    headers=rejected.headers,
                )
            elif websocket.application_state is WebSocketState.CONNECTED:
                if state is not None and state.scope.connection is not None:
                    state.scope.connection._close_code = REJECTION_CLOSES[
                        rejected.rejection
                    ]
                await websocket.close(
                    CLOSE_CODES[REJECTION_CLOSES[rejected.rejection]],
                    close_reason(rejected.reason),
                )

    return reject_failures


def _middleware_dependency(
    endpoint: RpcAnyEndpoint,
    middleware: Sequence[RpcMiddlewareLike],
    *,
    custom: bool = False,
) -> Callable[[WebSocket], AsyncGenerator[None, None]]:
    async def dependency(websocket: WebSocket) -> AsyncGenerator[None, None]:
        async with _serving_scope(
            endpoint, FastApiSocket(websocket), middleware
        ) as state:
            if state is None:
                yield
                return
            websocket.scope["fastapi_canon.integrations.websockets.serving"] = state
            try:
                async with (
                    _custom_connection(websocket, state) if custom else nullcontext()
                ):
                    yield
            finally:
                websocket.scope.pop(
                    "fastapi_canon.integrations.websockets.serving", None
                )

    return dependency


@asynccontextmanager
async def _custom_connection(
    websocket: WebSocket, state: _ServingState
) -> AsyncGenerator[None, None]:
    original_send, original_receive = websocket.send, websocket.receive
    connection: RpcConnection | None = None

    def disconnected(code: int, reason: str) -> None:
        if connection is not None and not connection.closed:
            close = RpcDisconnect(code, reason)
            connection._close_code = close.code
            connection._raw_close_code = close.raw_close_code
            connection._close_reason = close.reason
            connection._closed = True

    async def send(message: Message) -> None:
        nonlocal connection
        try:
            await original_send(message)
        except WebSocketDisconnect as error:
            disconnected(error.code, error.reason)
            raise
        # Delegated serving prepares its own connection and sends its callbacks.
        if message["type"] == "websocket.accept" and state.scope.connection is None:
            connection = RpcConnection._create(state.scope.endpoint, state.socket)
            connection._accepted = True
            state.scope.connection = connection
            state.accepted_at = time.perf_counter()
            await notify_activity(state.activity, "connection_opened", connection)
        elif message["type"] == "websocket.close":
            disconnected(message.get("code", 1000), message.get("reason") or "")

    async def receive() -> Message:
        message = await original_receive()
        if message["type"] == "websocket.disconnect":
            disconnected(message.get("code", 1000), message.get("reason") or "")
        return message

    async def observed_socket() -> WebSocket:
        nonlocal connection
        observed = WebSocket(websocket.scope, receive=receive, send=send)
        observed.client_state = websocket.client_state
        observed.application_state = websocket.application_state
        state.socket = FastApiSocket(observed)
        if (
            websocket.application_state is WebSocketState.CONNECTED
            and state.scope.connection is None
        ):
            connection = RpcConnection._create(state.scope.endpoint, state.socket)
            connection._accepted = True
            state.scope.connection = connection
            state.accepted_at = time.perf_counter()
            await notify_activity(state.activity, "connection_opened", connection)
        return observed

    websocket.scope[_OBSERVED_SOCKET_KEY] = observed_socket
    try:
        yield
    except WebSocketDisconnect as error:
        state.disconnect_error = error
        if state.error is error:
            state.error = None
        disconnected(error.code, error.reason)
        raise
    except BaseException as error:
        if connection is not None and not connection.closed:
            connection._close_code = (
                RpcConnectionClose.SHUTDOWN
                if isinstance(error, asyncio.CancelledError)
                else RpcConnectionClose.INTERNAL_ERROR
            )
        raise
    finally:
        websocket.scope.pop(_OBSERVED_SOCKET_KEY, None)
        if connection is not None:
            connection._closed = True
