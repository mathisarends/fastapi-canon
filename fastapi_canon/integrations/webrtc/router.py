"""``WebRtcRouter``: declare session endpoints on a FastAPI router.

This module evaluates annotations eagerly: FastAPI reads the generated
endpoint signature, whose dependency refers to a closure-local factory.
"""

import asyncio
import inspect
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from functools import update_wrapper
from typing import Annotated, Any, TypeVar, cast, get_type_hints

from dishka import AsyncContainer, DependencyKey
from dishka.integrations.base import default_parse_dependency, is_dishka_injected
from fastapi import APIRouter, Depends, HTTPException, WebSocket
from fastapi.params import Depends as DependsParam
from starlette.requests import HTTPConnection

from fastapi_canon.contract_routes import WEBRTC_DECLARATION_ATTRIBUTE
from fastapi_canon.injection import injected_dependency
from fastapi_canon.integrations.webrtc.capacity import SessionCapacity, SlotReservation
from fastapi_canon.integrations.webrtc.declarations import (
    AdmissionErrors,
    DataChannelSpec,
    MediaPolicy,
    MessageFormat,
    Role,
    Session,
    SessionDeclaration,
    SessionLimits,
)
from fastapi_canon.integrations.webrtc.engine import Engine
from fastapi_canon.integrations.webrtc.errors import ErrorCode, close_code
from fastapi_canon.integrations.webrtc.events import (
    EventHook,
    SessionEvent,
    SessionEventKind,
)
from fastapi_canon.integrations.webrtc.ice import IceServerSource
from fastapi_canon.integrations.webrtc.media import stop_native_track
from fastapi_canon.integrations.webrtc.runtime import (
    MINIMUM_CLEANUP_STEP_SECONDS,
    RuntimeOptions,
    SessionRuntime,
)

logger = logging.getLogger("fastapi_canon.integrations.webrtc")

Factory = TypeVar("Factory", bound=Callable[..., Any])

DECLARATION_ATTRIBUTE = WEBRTC_DECLARATION_ATTRIBUTE


class WebRtcRouter(APIRouter):
    """An ``APIRouter`` whose ``session`` decorator declares WebRTC sessions.

    Each session is a WebSocket signaling endpoint. Its factory is a regular
    FastAPI dependency: path and query parameters, authentication and other
    dependencies resolve before the WebSocket is accepted, so a rejected
    request allocates no peer. Dependencies, including ``yield`` dependencies,
    stay open until the session's cleanup finished.
    ``Inject[T]`` and ``FromDishka[T]`` resolve from the WebSocket's Dishka
    SESSION container automatically, without an HTTP request parameter.

    Including the router in an application registers its lifespan, which closes
    every active session with ``shutdown`` when the application stops.

    ``allowed_origins`` lists browser origins such as ``"https://app.example.com"``
    that may open sessions. Browsers attach cookies to cross-site WebSocket
    handshakes and CORS does not apply to them, so set it whenever session
    admission relies on cookies. A handshake from another origin is refused with
    HTTP 403 before the session factory runs. Handshakes without an ``Origin``
    header come from non-browser clients and are not affected. ``None`` (the
    default) accepts every origin.
    """

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        ice_servers: IceServerSource | None = None,
        limits: SessionLimits | None = None,
        max_sessions: int | None = None,
        on_event: EventHook | None = None,
        allowed_origins: Sequence[str] | None = None,
        prefix: str = "",
        tags: list[str] | None = None,
        dependencies: Sequence[DependsParam] | None = None,
    ) -> None:
        super().__init__(
            prefix=prefix,
            tags=list(tags or []),
            dependencies=list(dependencies or []),
            lifespan=self._lifespan,
        )
        self.engine = engine
        self.ice_servers = ice_servers
        self.limits = limits or SessionLimits()
        self.event_hook = on_event
        self.allowed_origins = _origins(allowed_origins)
        self.declarations: list[SessionDeclaration] = []
        self._capacity = _capacity(max_sessions)
        self._runtimes: set[SessionRuntime] = set()
        self._closing = False

    @property
    def active_sessions(self) -> int:
        return len(self._runtimes)

    def session(
        self,
        path: str,
        *,
        name: str | None = None,
        role: Role = Role.ANSWERER,
        media: MediaPolicy | None = None,
        data_channels: Sequence[DataChannelSpec] = (),
        limits: SessionLimits | None = None,
        max_sessions: int | None = None,
        capacity: SessionCapacity | None = None,
        dependencies: Sequence[DependsParam] | None = None,
        admission_errors: AdmissionErrors | None = None,
    ) -> Callable[[Factory], Factory]:
        """Declare a session endpoint whose factory returns a :class:`Session`.

        The factory may be a function, coroutine function or generator
        dependency. ``name`` defaults to the factory name and becomes the
        generated client's method name. ``max_sessions`` limits this endpoint;
        ``capacity`` is a :class:`SessionCapacity` shared with other declarations.
        """
        raw_capacity: object = capacity
        if raw_capacity is not None and not isinstance(raw_capacity, SessionCapacity):
            raise TypeError("capacity must be a SessionCapacity instance or None")
        local = _capacity(max_sessions)
        capacities = tuple(
            value for value in (self._capacity, local, capacity) if value is not None
        )

        def decorator(factory: Factory) -> Factory:
            dependency, roots = _inject_factory(factory)
            declaration = SessionDeclaration(
                name=name or factory.__name__,
                path=self.prefix + path,
                role=role,
                media=media or MediaPolicy(),
                channels=tuple(data_channels),
                limits=limits or self.limits,
                max_sessions=max_sessions,
                description=inspect.getdoc(factory),
                admission_errors=admission_errors,
            )
            if any(d.name == declaration.name for d in self.declarations):
                raise ValueError(f"Session {declaration.name!r} is already declared")
            self.declarations.append(declaration)

            async def endpoint(
                websocket: WebSocket,
                session: Annotated[Session, Depends(dependency)],
            ) -> None:
                if not isinstance(cast(object, session), Session):
                    logger.error(
                        "Session factory %s returned %s instead of Session",
                        declaration.name,
                        type(session).__name__,
                    )
                    await _refuse(websocket, ErrorCode.HANDLER_FAILURE)
                    return
                await self._serve(declaration, capacities, websocket, session)

            endpoint.__doc__ = factory.__doc__
            endpoint.__name__ = f"webrtc_session_{declaration.name}"
            setattr(endpoint, DECLARATION_ATTRIBUTE, declaration)
            endpoint.__dict__["__canon_webrtc_dependency_roots__"] = roots
            self.add_api_websocket_route(
                path,
                endpoint,
                name=declaration.name,
                dependencies=[Depends(self._check_origin), *(dependencies or ())],
            )
            return factory

        return decorator

    async def aclose(self, timeout: float | None = None) -> None:
        """Close every active session with ``shutdown`` and wait for cleanup.

        New sessions are refused afterwards. ``timeout`` defaults to the largest
        configured shutdown deadline plus the minimum budgets of ``on_close`` and
        engine cleanup.
        """
        self._closing = True
        runtimes = list(self._runtimes)
        for runtime in runtimes:
            runtime.terminate(ErrorCode.SHUTDOWN)
        if not runtimes:
            return
        if timeout is None:
            timeout = 2 * MINIMUM_CLEANUP_STEP_SECONDS + max(
                runtime.declaration.limits.shutdown_timeout_seconds
                for runtime in runtimes
            )
        waiters = [asyncio.ensure_future(r.wait_finished()) for r in runtimes]
        _, pending = await asyncio.wait(waiters, timeout=timeout)
        for waiter in pending:
            waiter.cancel()
        if pending:
            logger.warning("%d WebRTC session(s) did not finish cleanup", len(pending))

    async def _serve(
        self,
        declaration: SessionDeclaration,
        capacities: tuple[SessionCapacity, ...],
        websocket: WebSocket,
        session: Session,
    ) -> None:
        runtime: SessionRuntime | None = None
        try:
            if session.engine is None and self.engine is None:
                logger.error("Session %s has no engine binding", declaration.name)
                await _refuse(websocket, ErrorCode.HANDLER_FAILURE)
                return
            unknown = _invalid_handlers(declaration, session)
            if unknown:
                logger.error(
                    "Session %s has handlers for undeclared or send-only channels: %s",
                    declaration.name,
                    ", ".join(sorted(unknown)),
                )
                await _refuse(websocket, ErrorCode.HANDLER_FAILURE)
                return
            runtime = self._admit(declaration, capacities, websocket, session)
            if runtime is None:
                code = ErrorCode.SHUTDOWN if self._closing else ErrorCode.SESSION_LIMIT
                await _refuse(websocket, code)
                return
            self._runtimes.add(runtime)
            await runtime.serve()
        finally:
            if runtime is None:
                # A returned Session transfers its tracks even if admission fails.
                for track in {id(t): t for t in session.tracks}.values():
                    stop_native_track(track)
            else:
                self._runtimes.discard(runtime)

    def _check_origin(self, connection: HTTPConnection) -> None:
        """Refuse cross-site handshakes before the session factory runs."""
        allowed = self.allowed_origins
        origin = connection.headers.get("origin")
        if allowed is None or origin is None:
            return
        if origin.lower().rstrip("/") not in allowed:
            logger.warning("Refused WebRTC session from origin %r", origin)
            raise HTTPException(status_code=403, detail="Origin not allowed")

    def _admit(
        self,
        declaration: SessionDeclaration,
        capacities: tuple[SessionCapacity, ...],
        websocket: WebSocket,
        session: Session,
    ) -> SessionRuntime | None:
        session_id = uuid.uuid4().hex
        if self._closing:
            return None
        if any(capacity.full for capacity in capacities):
            self._emit(
                SessionEvent(
                    kind=SessionEventKind.REJECTED,
                    name=declaration.name,
                    session_id=session_id,
                    elapsed_seconds=0.0,
                    code=ErrorCode.SESSION_LIMIT,
                )
            )
            return None
        return SessionRuntime(
            declaration=declaration,
            options=RuntimeOptions(
                engine=cast(
                    Engine,
                    session.engine if session.engine is not None else self.engine,
                ),
                ice_servers=self.ice_servers,
                on_event=self.event_hook,
            ),
            websocket=websocket,
            session=session,
            session_id=session_id,
            slots=SlotReservation(*capacities),
        )

    def _emit(self, event: SessionEvent) -> None:
        if self.event_hook is None:
            return
        try:
            self.event_hook(event)
        except Exception:
            logger.exception("Session event hook failed")

    @asynccontextmanager
    async def _lifespan(self, _app: Any) -> AsyncIterator[None]:
        self._closing = False
        try:
            yield
        finally:
            await self.aclose()


def _inject_factory(
    factory: Callable[..., Any],
) -> tuple[Callable[..., Any], tuple[DependencyKey, ...] | None]:
    source = getattr(factory, "__dishka_orig_func__", factory)
    signature = inspect.signature(source)
    hints = get_type_hints(source, include_extras=True)
    roots: list[DependencyKey] = []
    parameters: list[inspect.Parameter] = []
    for name, parameter in signature.parameters.items():
        hint = hints.get(name, parameter.annotation)
        service = injected_dependency(hint)
        key = (
            DependencyKey(service, "")
            if service is not None
            else default_parse_dependency(parameter, hint)
        )
        if key is not None:
            roots.append(key)
            hint = Annotated[key.type_hint, Depends(_factory_resolver(key))]
        parameters.append(parameter.replace(annotation=hint))
    if is_dishka_injected(factory):
        # Dishka's synchronous decorator replaces markers in place; its Depends
        # closures belong to Dishka and cannot be statically recovered here.
        return factory, tuple(dict.fromkeys(roots)) if roots else None
    if not roots:
        return factory, ()
    if inspect.isasyncgenfunction(factory):
        manager = asynccontextmanager(factory)

        async def async_generator(*args: Any, **kwargs: Any) -> Any:
            async with manager(*args, **kwargs) as session:
                yield session

        wrapped: Callable[..., Any] = async_generator
    elif inspect.iscoroutinefunction(factory):

        async def coroutine(*args: Any, **kwargs: Any) -> Any:
            return await factory(*args, **kwargs)

        wrapped = coroutine
    elif inspect.isgeneratorfunction(factory):

        def generator(*args: Any, **kwargs: Any) -> Any:
            yield from factory(*args, **kwargs)

        wrapped = generator
    else:

        def function(*args: Any, **kwargs: Any) -> Any:
            return factory(*args, **kwargs)

        wrapped = function
    update_wrapper(wrapped, factory)
    wrapped.__dict__["__signature__"] = signature.replace(
        parameters=parameters,
        return_annotation=hints.get("return", signature.return_annotation),
    )
    wrapped.__annotations__ = {
        **hints,
        **{parameter.name: parameter.annotation for parameter in parameters},
    }
    return wrapped, tuple(dict.fromkeys(roots))


def _factory_resolver(key: DependencyKey) -> Callable[..., Any]:
    async def resolve(websocket: WebSocket) -> Any:
        container: AsyncContainer | None = getattr(
            websocket.state, "dishka_container", None
        )
        if container is None:
            raise RuntimeError(
                "WebRTC Inject/FromDishka factories need a Dishka WebSocket "
                "SESSION container; install with Composition or setup_dishka."
            )
        return await container.get(key.type_hint, component=key.component)

    return resolve


def _capacity(max_sessions: int | None) -> SessionCapacity | None:
    return None if max_sessions is None else SessionCapacity(max_sessions=max_sessions)


def _origins(origins: Sequence[str] | None) -> frozenset[str] | None:
    if origins is None:
        return None
    if isinstance(origins, str):
        raise TypeError("allowed_origins must be a sequence of origins, not a str")
    normalized = set()
    for origin in origins:
        value = origin.strip().lower().rstrip("/")
        scheme, separator, host = value.partition("://")
        if not separator or not scheme or not host or "/" in host:
            raise ValueError(
                f"Invalid origin {origin!r}; use scheme://host[:port], for "
                "example 'https://app.example.com'"
            )
        normalized.add(value)
    return frozenset(normalized)


def _invalid_handlers(declaration: SessionDeclaration, session: Session) -> set[str]:
    """Handler keys that name no receiving channel or use a different spec of the
    same name."""
    declared = {
        spec.name: spec
        for spec in declaration.channels
        if spec.receive_format != MessageFormat.NONE
    }
    unknown = set(session.message_handlers) - declared.keys()
    for key in session.on_message:
        if isinstance(key, DataChannelSpec) and declared.get(key.name) != key:
            unknown.add(key.name)
    return unknown


async def _refuse(websocket: WebSocket, code: ErrorCode) -> None:
    """Accept only to deliver a categorized close code to the browser."""
    await websocket.accept(subprotocol=_subprotocol(websocket))
    await websocket.close(code=close_code(code), reason=code.value)


def _subprotocol(websocket: WebSocket) -> str | None:
    from fastapi_canon.integrations.webrtc.protocol import SUBPROTOCOL

    offered = websocket.scope.get("subprotocols") or []
    return SUBPROTOCOL if SUBPROTOCOL in offered else None
