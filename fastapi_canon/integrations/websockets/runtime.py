from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, nullcontext, suppress
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from pydantic import BaseModel, ValidationError

from fastapi_canon.integrations.websockets.activity import Activity, notify_activity
from fastapi_canon.integrations.websockets.adapter import adapter
from fastapi_canon.integrations.websockets.codec import RpcCodec
from fastapi_canon.integrations.websockets.connection import (
    REJECTION_CLOSES,
    STARTUP_EXPIRED,
    RpcBeforeAccept,
    RpcConnection,
    RpcConnectionClose,
    RpcConnectionExpiry,
    RpcDisconnect,
    RpcLimits,
    RpcReject,
    RpcRejection,
    RpcRejections,
    RpcWebSocket,
    rejection_for,
)
from fastapi_canon.integrations.websockets.constants import LOGGER_NAME
from fastapi_canon.integrations.websockets.definition import (
    RpcAnyEndpoint,
    RpcEndpoint,
    RpcSocketEndpoint,
    RpcStreamEndpoint,
)
from fastapi_canon.integrations.websockets.dependencies import (
    RpcResolver,
    RpcResolverLike,
    as_resolver,
    connection_scope,
    context_values,
)
from fastapi_canon.integrations.websockets.drain import current_drain_session
from fastapi_canon.integrations.websockets.envelopes import (
    RpcFailure,
    RpcNotification,
    RpcRequestEnvelope,
    RpcSuccess,
)
from fastapi_canon.integrations.websockets.errors import (
    RpcError,
    RpcInternalError,
    RpcInvalidRequestError,
    RpcParseError,
    RpcRequestCancelledError,
    RpcServerShuttingDownError,
    bind_contracts,
    contract_for,
    contract_of,
)
from fastapi_canon.integrations.websockets.middleware import (
    RpcMiddlewareLike,
    RpcStreamScope,
    current_serving,
    record_error,
    record_rejection,
    serving_scope,
    stream_scope,
)
from fastapi_canon.integrations.websockets.protocol import RpcNotificationDefinition
from fastapi_canon.integrations.websockets.server import (
    CLIENT_CANCEL,
    RpcErrorMapper,
    RpcServer,
)
from fastapi_canon.integrations.websockets.sockets import RpcMessageType, RpcSocket
from fastapi_canon.integrations.websockets.streams import (
    RpcBinaryInput,
    RpcBinaryOutput,
    RpcInputEndMessage,
    RpcStreamClose,
)
from fastapi_canon.integrations.websockets.subscriptions import SubscriptionSession

logger: logging.Logger = logging.getLogger(LOGGER_NAME)


def map_rejection(
    error: Exception, rejections: RpcRejections | None
) -> RpcReject | None:
    try:
        return rejection_for(error, rejections)
    except Exception:
        logger.exception("RPC rejection mapper failed")
        return None


async def serve_endpoint(
    endpoint: RpcEndpoint[Any],
    socket: RpcWebSocket,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    error_mapper: RpcErrorMapper | None = None,
    limits: RpcLimits | None = None,
    before_accept: RpcBeforeAccept | None = None,
    rejections: RpcRejections | None = None,
    middleware: Sequence[RpcMiddlewareLike] = (),
) -> None:
    async with (
        endpoint.service.drain.session(socket) as session,
        serving_scope(endpoint, socket, middleware) as state,
    ):
        if session is None:
            record_rejection(RpcRejection.UNAVAILABLE)
            return
        await _serve_endpoint(
            endpoint,
            socket,
            resolver=resolver,
            context=context,
            error_mapper=error_mapper,
            limits=limits,
            before_accept=before_accept,
            rejections=rejections,
            activity=None if state is None else state.activity,
            middleware=() if state is None else state.middleware,
        )


class RpcPendingLimitError(RpcError):
    _builtin = True
    code = "pending_limit"
    message = "Too many pending requests"


async def serve_stream_endpoint(
    endpoint: RpcStreamEndpoint[Any],
    socket: RpcWebSocket,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    limits: RpcLimits | None = None,
    before_accept: RpcBeforeAccept | None = None,
    error_mapper: RpcErrorMapper | None = None,
    rejections: RpcRejections | None = None,
    middleware: Sequence[RpcMiddlewareLike] = (),
) -> None:
    async with (
        endpoint.service.drain.session(socket) as session,
        serving_scope(endpoint, socket, middleware) as state,
    ):
        if session is None:
            record_rejection(RpcRejection.UNAVAILABLE)
            return
        await _serve_stream_endpoint(
            endpoint,
            socket,
            resolver=resolver,
            context=context,
            error_mapper=error_mapper,
            limits=limits,
            before_accept=before_accept,
            rejections=rejections,
            activity=None if state is None else state.activity,
            middleware=() if state is None else state.middleware,
        )


async def serve_socket_endpoint(
    endpoint: RpcSocketEndpoint[Any],
    socket: RpcWebSocket,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    limits: RpcLimits | None = None,
    before_accept: RpcBeforeAccept | None = None,
    rejections: RpcRejections | None = None,
    middleware: Sequence[RpcMiddlewareLike] = (),
) -> None:
    async with (
        endpoint.service.drain.session(socket) as session,
        serving_scope(endpoint, socket, middleware) as state,
    ):
        if session is None:
            record_rejection(RpcRejection.UNAVAILABLE)
            return
        prepared = await _prepare(
            endpoint,
            socket,
            resolver,
            context,
            endpoint.socket.path_model,
            before_accept,
            rejections,
        )
        if prepared is None:
            return
        connection, resolved, values, path_values = prepared
        activity = None if state is None else state.activity
        async with _accepted(socket, connection):
            await notify_activity(activity, "connection_opened", connection)
            await _run_socket(
                endpoint,
                socket,
                connection,
                resolved,
                values,
                path_values,
                limits or RpcLimits(),
                rejections,
                activity,
            )


async def _prepare(
    endpoint: RpcAnyEndpoint,
    socket: RpcWebSocket,
    resolver: RpcResolverLike | None,
    context: object | Mapping[type[Any], object] | None,
    path_model: type[BaseModel] | None = None,
    before_accept: RpcBeforeAccept | None = None,
    rejections: RpcRejections | None = None,
) -> tuple[RpcConnection, RpcResolver, dict[type[Any], object], dict[str, Any]] | None:
    endpoint.service.freeze()
    resolved = as_resolver(resolver)
    values = context_values(context)
    path_params = (
        socket.handshake.path_params or endpoint.match(socket.handshake.path) or {}
    )
    connection = RpcConnection.create(endpoint.name, socket, path_params)
    drain_session = current_drain_session()
    if drain_session is not None:
        drain_session.connection = connection
        if not isinstance(endpoint, RpcEndpoint):
            drain_session.ready.set()
    state = current_serving()
    if state is not None:
        state.scope.connection = connection
    base_values = {**values, RpcConnection: connection}
    if (
        endpoint.subprotocol is not None
        and endpoint.subprotocol not in socket.handshake.subprotocols
    ):
        record_rejection(RpcRejection.PROTOCOL_ERROR)
        await socket.reject(RpcRejection.PROTOCOL_ERROR, "Unsupported subprotocol")
        return None
    path_values: dict[str, Any] = {}
    if path_model is not None:
        try:
            parsed = path_model.model_validate(dict(path_params))
        except ValidationError:
            record_rejection(RpcRejection.NOT_FOUND)
            await socket.reject(RpcRejection.NOT_FOUND, "Invalid path variable")
            return None
        path_values = {name: getattr(parsed, name) for name in path_model.model_fields}
        base_values[path_model] = parsed
    if before_accept is not None:
        try:
            accepted_values = await before_accept(socket.handshake)
        except Exception as error:
            record_error(error)
            rejected = map_rejection(error, rejections)
            if rejected is None:
                logger.exception("RPC before_accept hook failed")
                rejected = RpcReject(RpcRejection.INTERNAL_ERROR, "Internal error")
            record_rejection(rejected.rejection)
            await socket.reject(
                rejected.rejection, rejected.reason, headers=rejected.headers
            )
            return None
        if accepted_values is not None:
            base_values.update(accepted_values)
    expiry = base_values.get(RpcConnectionExpiry)
    if isinstance(expiry, RpcConnectionExpiry):
        connection.expire_at(expiry.expires_at)
    if endpoint.service.drain.draining:
        record_rejection(RpcRejection.UNAVAILABLE)
        await socket.reject(RpcRejection.UNAVAILABLE, "Server restarting")
        return None
    await socket.accept(endpoint.subprotocol)
    connection.mark_accepted()
    if state is not None:
        state.accepted_at = time.perf_counter()
    return connection, resolved, base_values, path_values


async def _serve_endpoint(
    endpoint: RpcEndpoint[Any],
    socket: RpcWebSocket,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    error_mapper: RpcErrorMapper | None = None,
    limits: RpcLimits | None = None,
    before_accept: RpcBeforeAccept | None = None,
    rejections: RpcRejections | None = None,
    activity: Activity | None = None,
    middleware: Sequence[RpcMiddlewareLike] = (),
) -> None:
    limits = limits or RpcLimits()
    prepared = await _prepare(
        endpoint,
        socket,
        resolver,
        context,
        endpoint.path_model,
        before_accept,
        rejections,
    )
    if prepared is None:
        return
    connection, resolved, values, _ = prepared
    async with _accepted(socket, connection):
        await notify_activity(activity, "connection_opened", connection)
        await _run_rpc_endpoint(
            endpoint,
            socket,
            connection,
            resolved,
            values,
            error_mapper,
            limits,
            rejections,
            activity,
            middleware,
        )


async def _run_rpc_endpoint(
    endpoint: RpcEndpoint[Any],
    socket: RpcWebSocket,
    connection: RpcConnection,
    resolved: RpcResolver,
    values: dict[type[Any], object],
    error_mapper: RpcErrorMapper | None,
    limits: RpcLimits,
    rejections: RpcRejections | None,
    activity: Activity | None,
    middleware: Sequence[RpcMiddlewareLike],
) -> None:
    await _RpcSession(
        endpoint,
        socket,
        connection,
        resolved,
        values,
        error_mapper,
        limits,
        rejections,
        activity,
        middleware,
    ).run()


class _RpcSession:
    def __init__(
        self,
        endpoint: RpcEndpoint[Any],
        socket: RpcWebSocket,
        connection: RpcConnection,
        resolved: RpcResolver,
        values: dict[type[Any], object],
        error_mapper: RpcErrorMapper | None,
        limits: RpcLimits,
        rejections: RpcRejections | None,
        activity: Activity | None,
        middleware: Sequence[RpcMiddlewareLike],
    ) -> None:
        self.endpoint = endpoint
        self.socket = socket
        self.connection = connection
        self.resolved = resolved
        self.values = values
        self.error_mapper = error_mapper
        self.limits = limits
        self.rejections = rejections
        self.activity = activity
        self.middleware = middleware
        self.close_event = asyncio.Event()
        self.close_value: tuple[RpcConnectionClose, str] = (
            RpcConnectionClose.NORMAL,
            "",
        )
        self.client_closed = False
        self.outgoing: asyncio.Queue[str] = asyncio.Queue(self.limits.max_queue_size)
        self.slow_consumer_reported = False
        self.pending_rejections = 0
        self.pending = _PendingRequests(self.limits.max_pending_requests)
        self.codec = RpcCodec()
        self.connection.set_close_handler(self.request_close)
        self.tasks: set[asyncio.Task[None]] = set()
        self.call_tasks: set[asyncio.Task[RpcSuccess | RpcFailure | None]] = set()
        self.requests: dict[tuple[type[str] | type[int], str | int], _PendingCall] = {}

    async def run(self) -> None:
        try:
            async with connection_scope(self.resolved, self.values) as self.scoped:
                self.server = RpcServer.from_channel(
                    self.endpoint.protocol,
                    resolver=self.scoped,
                    error_mapper=self.error_mapper,
                    middleware=self.middleware,
                    endpoint=self.endpoint,
                    connection=self.connection,
                    limits=self.limits,
                    strict_errors=self.endpoint.service.strict_errors,
                )
                self.subscriptions = SubscriptionSession(
                    self.endpoint.protocol.subscriptions,
                    self.scoped,
                    self._send_outgoing,
                    limit=self.limits.max_streams,
                    server=self.server,
                    activity=self.activity,
                )
                self.writer_task = asyncio.create_task(self._writer())
                self.producers = [
                    asyncio.create_task(self._reader()),
                    asyncio.create_task(self._events()),
                ]
                drain_session = current_drain_session()
                if drain_session is not None:
                    drain_session.begin = self._graceful
                    drain_session.ready.set()
                try:
                    self.connection.track_startup(None)
                    await self.close_event.wait()
                    for task in (*self.producers, *self.tasks, *self.call_tasks):
                        task.cancel()
                    await asyncio.gather(
                        *self.producers,
                        *self.tasks,
                        *self.call_tasks,
                        return_exceptions=True,
                    )
                    await self.subscriptions.close()
                    if self.close_value[0] in (
                        RpcConnectionClose.NORMAL,
                        RpcConnectionClose.SHUTDOWN,
                    ):
                        await _flush(
                            self.outgoing, self.writer_task, self.limits.send_timeout
                        )
                finally:
                    for task in (
                        self.writer_task,
                        *self.producers,
                        *self.tasks,
                        *self.call_tasks,
                    ):
                        task.cancel()
                    try:
                        await asyncio.gather(
                            self.writer_task,
                            *self.producers,
                            *self.tasks,
                            *self.call_tasks,
                            return_exceptions=True,
                        )
                    finally:
                        await self.subscriptions.close()
        except asyncio.CancelledError:
            shutdown = self.connection.close_code or RpcConnectionClose.SHUTDOWN
            self.close_value = (shutdown, self.connection.close_reason)
            self.connection.record_close(shutdown, self.connection.close_reason)
            if not self.client_closed:
                with suppress(RpcDisconnect):
                    await self.socket.close(*self.close_value)
            self.connection.mark_closed()
            raise
        finally:
            if self.pending_rejections:
                logger.debug(
                    "Rejected %d messages exceeding max_pending_requests "
                    "on this connection",
                    self.pending_rejections,
                )
            if not self.client_closed and (not self.connection.closed):
                with suppress(RpcDisconnect):
                    await self.socket.close(*self.close_value)
            self.connection.mark_closed()

    def request_close(
        self, code: RpcConnectionClose, reason: str, raw_close_code: int | None = None
    ) -> None:
        if not self.close_event.is_set():
            self.close_value = (code, reason)
            self.connection.record_close(code, reason, raw_close_code)
            self.close_event.set()

    async def _report_slow_consumer(self) -> None:
        if self.slow_consumer_reported:
            return
        self.slow_consumer_reported = True
        await notify_activity(self.activity, "slow_consumer_closed", self.connection)

    async def _send_outgoing(self, message: str) -> None:
        try:
            async with asyncio.timeout(self.limits.send_timeout):
                await self.outgoing.put(message)
        except TimeoutError:
            logger.warning("RPC client too slow: outgoing queue blocked")
            self.request_close(RpcConnectionClose.POLICY_VIOLATION, "Client too slow")
            await self._report_slow_consumer()

    async def _writer(self) -> None:
        try:
            while True:
                message = await self.outgoing.get()
                try:
                    async with asyncio.timeout(self.limits.send_timeout):
                        await self.socket.send(message)
                finally:
                    self.outgoing.task_done()
        except asyncio.CancelledError:
            raise
        except RpcDisconnect as error:
            self.client_closed = True
            self.request_close(error.code, error.reason, error.raw_close_code)
        except TimeoutError as error:
            record_error(error)
            logger.warning("RPC client too slow: socket send blocked")
            self.request_close(RpcConnectionClose.POLICY_VIOLATION, "Client too slow")
            await self._report_slow_consumer()
        except Exception as error:
            record_error(error)
            logger.exception("RPC writer failed")
            self.request_close(RpcConnectionClose.INTERNAL_ERROR, "Internal error")

    async def _invoke_one(self, call: _PendingCall) -> RpcSuccess | RpcFailure | None:
        call.started = True
        message = call.message
        try:
            if call.invalid:
                return await self.server.handle_failure(
                    message, RpcInvalidRequestError()
                )
            if call.shutting_down:
                return await self.server.handle_failure(
                    message, RpcServerShuttingDownError()
                )
            if call.reservation is None:
                return await self._reject_pending(message)
            if not call.batch and self.subscriptions.owns(message["method"]):
                assert call.envelope is not None
                return await self.subscriptions.handle(
                    call.envelope, admitted_at=call.admitted_at
                )
            return await self.server.handle_one_bounded(
                message, admitted_at=call.admitted_at, envelope=call.envelope
            )
        finally:
            if call.reservation is not None:
                self.pending.release(call.reservation)

    async def _collect_one(self, call: _PendingCall) -> RpcSuccess | RpcFailure | None:
        try:
            return await call.task
        except asyncio.CancelledError:
            if (
                call.started
                or not call.cancel_requested
                or self.close_event.is_set()
                or (
                    (current := asyncio.current_task()) is not None
                    and current.cancelling()
                )
            ):
                raise
            return await self.server.handle_failure(
                call.message, RpcRequestCancelledError()
            )

    async def _invoke(
        self,
        items: Sequence[_PendingCall],
        batch: bool,
        controls: Sequence[RpcSuccess | RpcFailure] = (),
    ) -> None:
        try:
            responses = (
                [await self._collect_one(items[0])]
                if len(items) == 1
                else await asyncio.gather(*(self._collect_one(call) for call in items))
            )
        finally:
            unfinished = [call.task for call in items if not call.task.done()]
            for task in unfinished:
                task.cancel()
            if unfinished:
                await asyncio.gather(*unfinished, return_exceptions=True)
        completed: list[BaseModel] = [
            *controls,
            *(r for r in responses if r is not None),
        ]
        if completed and (not self.close_event.is_set()):
            await self._send_outgoing(
                self.codec.encode(completed if batch else completed[0])
            )

    async def _reject_pending(self, message: object) -> RpcSuccess | RpcFailure | None:
        self.pending_rejections += 1
        if self.pending_rejections == 1:
            logger.warning("RPC client exceeded max_pending_requests")
        return await self.server.handle_failure(message, RpcPendingLimitError())

    def _invocation_done(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            record_error(error)
            self.request_close(RpcConnectionClose.INTERNAL_ERROR, "Internal error")

    def _schedule_failure(self, message: object, error: Exception) -> bool:
        if (
            len(self.tasks)
            >= self.limits.max_pending_requests + self.limits.max_queue_size
        ):
            self.request_close(
                RpcConnectionClose.POLICY_VIOLATION, "Too many response collectors"
            )
            return False

        async def respond() -> None:
            response = await self.server.handle_failure(message, error)
            if response is not None and (not self.close_event.is_set()):
                await self._send_outgoing(self.codec.encode(response))

        task = asyncio.create_task(respond())
        self.tasks.add(task)
        task.add_done_callback(self._invocation_done)
        return True

    def _call_done(
        self,
        call: _PendingCall,
        key: tuple[type[str] | type[int], str | int] | None,
        task: asyncio.Task[RpcSuccess | RpcFailure | None],
    ) -> None:
        self.call_tasks.discard(call.task)
        if call.reservation is not None:
            self.pending.release(call.reservation)
        if key is not None and self.requests.get(key) is call:
            del self.requests[key]

    def _cancel_request(self, item: object) -> bool:
        if not isinstance(item, dict) or item.get("method") != "$/cancelRequest":
            return False
        if "id" in item:
            return False
        params = item.get("params")
        if (
            item.get("jsonrpc") == "2.0"
            and set(item) == {"jsonrpc", "method", "params"}
            and isinstance(params, dict)
            and (set(params) == {"id"})
            and (type(params["id"]) in (str, int))
        ):
            call = self.requests.get((type(params["id"]), params["id"]))
            if (
                call is not None
                and call.reservation is not None
                and (not call.cancel_requested)
                and (not call.task.done())
            ):
                call.cancel_requested = True
                call.task.cancel(CLIENT_CANCEL)
        return True

    async def _reader(self) -> None:
        try:
            while True:
                frame = await self.socket.receive()
                size = len(frame if isinstance(frame, bytes) else frame.encode())
                if (
                    self.limits.max_message_bytes is not None
                    and size > self.limits.max_message_bytes
                ):
                    self.request_close(RpcConnectionClose.MESSAGE_TOO_BIG, "")
                    return
                if isinstance(frame, bytes):
                    try:
                        frame = frame.decode()
                    except UnicodeDecodeError:
                        self.request_close(
                            RpcConnectionClose.PROTOCOL_ERROR, "Invalid RPC frame"
                        )
                        return
                try:
                    message = self.codec.decode(frame)
                except RpcParseError as error:
                    if not self._schedule_failure(frame, error):
                        return
                    continue
                batch = isinstance(message, list)
                if isinstance(message, list) and (
                    not message or len(message) > self.limits.max_batch_size
                ):
                    if not self._schedule_failure(message, RpcInvalidRequestError()):
                        return
                    continue
                items = []
                controls = []
                collectors_full = (
                    len(self.tasks)
                    >= self.limits.max_pending_requests + self.limits.max_queue_size
                )
                for item in message if isinstance(message, list) else [message]:
                    if self._cancel_request(item):
                        continue
                    if isinstance(item, dict) and item.get("method") == "$/ping":
                        valid = (
                            item.get("jsonrpc") == "2.0"
                            and set(item) <= {"jsonrpc", "method", "id", "params"}
                            and (item.get("params", {}) == {})
                            and ("id" not in item or type(item["id"]) in (str, int))
                        )
                        if "id" in item:
                            response = (
                                RpcSuccess(id=item["id"], result=None)
                                if valid
                                else RpcFailure.from_error(
                                    None, RpcInvalidRequestError()
                                )
                            )
                            controls.append(response)
                        continue
                    if collectors_full:
                        self.request_close(
                            RpcConnectionClose.POLICY_VIOLATION,
                            "Too many response collectors",
                        )
                        return
                    try:
                        envelope = RpcRequestEnvelope.model_validate(item)
                        if envelope.method == "$/cancelRequest":
                            raise ValueError("Cancellation must be a notification")
                    except (ValidationError, ValueError):
                        envelope = None
                        invalid = True
                        reservation = None
                    else:
                        invalid = False
                        reservation = (
                            None
                            if self.endpoint.service.drain.draining
                            else self.pending.reserve()
                        )
                    key = None
                    if (
                        not invalid
                        and isinstance(item, dict)
                        and type(item.get("id")) in (str, int)
                    ):
                        key = (type(item["id"]), item["id"])
                        if key in self.requests:
                            if reservation is not None:
                                self.pending.release(reservation)
                            self.request_close(
                                RpcConnectionClose.PROTOCOL_ERROR,
                                "Duplicate in-flight request id",
                            )
                            return
                    call = _PendingCall(
                        item,
                        reservation,
                        invalid,
                        batch,
                        asyncio.get_running_loop().time(),
                        shutting_down=not invalid
                        and self.endpoint.service.drain.draining,
                        envelope=envelope,
                    )
                    call.task = asyncio.create_task(self._invoke_one(call))
                    self.call_tasks.add(call.task)
                    if key is not None:
                        self.requests[key] = call
                    call.task.add_done_callback(partial(self._call_done, call, key))
                    items.append(call)
                if not items and (not controls):
                    continue
                if (
                    len(self.tasks)
                    >= self.limits.max_pending_requests + self.limits.max_queue_size
                ):
                    self.request_close(
                        RpcConnectionClose.POLICY_VIOLATION,
                        "Too many response collectors",
                    )
                    return
                task = asyncio.create_task(self._invoke(items, batch, controls))
                self.tasks.add(task)
                task.add_done_callback(self._invocation_done)
        except RpcDisconnect as error:
            self.client_closed = True
            self.request_close(error.code, error.reason, error.raw_close_code)
        except Exception as error:
            record_error(error)
            self.request_close(RpcConnectionClose.INTERNAL_ERROR, "Internal error")

    async def _events(self) -> None:
        try:
            await asyncio.gather(
                *(
                    _event_source(
                        event,
                        self.scoped,
                        self._send_outgoing,
                        self.activity,
                        self.rejections,
                        self.server,
                    )
                    for event in self.endpoint.protocol.notifications
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            record_error(error)
            rejected = map_rejection(error, self.rejections)
            if rejected is not None:
                self.request_close(
                    REJECTION_CLOSES[rejected.rejection], rejected.reason
                )
                return
            logger.exception("RPC event source failed")
            self.request_close(RpcConnectionClose.INTERNAL_ERROR, "Internal error")
            return
        if self.endpoint.close_when_events_complete:
            self.request_close(RpcConnectionClose.NORMAL, "")

    async def _graceful(self) -> None:
        self.producers[1].cancel()
        await asyncio.gather(self.producers[1], return_exceptions=True)
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        await self.subscriptions.wait_finished()
        self.producers[0].cancel()
        await asyncio.gather(
            self.producers[0], *tuple(self.tasks), return_exceptions=True
        )
        await _flush(self.outgoing, self.writer_task, None)
        self.request_close(RpcConnectionClose.SERVICE_RESTART, "Server restarting")


@dataclass
class _PendingCall:
    message: Any
    reservation: object | None
    invalid: bool
    batch: bool
    admitted_at: float
    shutting_down: bool = False
    # Validated once by the reader and reused for dispatch.
    envelope: RpcRequestEnvelope | None = None
    task: asyncio.Task[RpcSuccess | RpcFailure | None] = field(init=False)
    started: bool = False
    cancel_requested: bool = False


class _PendingRequests:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._reservations: set[object] = set()

    @property
    def count(self) -> int:
        return len(self._reservations)

    def reserve(self) -> object | None:
        if self.count >= self.limit:
            return None
        reservation = object()
        self._reservations.add(reservation)
        return reservation

    def release(self, reservation: object) -> None:
        self._reservations.discard(reservation)


async def _flush(
    outgoing: asyncio.Queue[str], writer: asyncio.Task[None], timeout: float | None
) -> None:
    drained = asyncio.create_task(outgoing.join())
    try:
        await asyncio.wait(
            (drained, writer), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        drained.cancel()


async def _event_source(
    event: RpcNotificationDefinition,
    resolver: RpcResolver,
    send: Callable[[str], Awaitable[None]],
    activity: Activity | None,
    rejections: RpcRejections | None,
    server: RpcServer,
) -> None:
    try:
        arguments = {
            parameter.name: await resolver.resolve(parameter.dependency)
            for parameter in event.injected_parameters
        }
        payload_adapter = adapter(event.payload)
        codec = RpcCodec()
        assert event.function is not None
        async for payload in event.function(**arguments):
            try:
                value = payload_adapter.validate_python(payload)
                message = codec.encode(
                    RpcNotification.with_payload_annotation(
                        event.name, value, event.payload
                    )
                )
            except Exception:
                logger.exception("RPC event %s produced an invalid payload", event.name)
                if event.on_error == "close":
                    raise
                continue
            await send(message)
            await notify_activity(
                activity, "notification_sent", event.name, len(message.encode())
            )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        binding = contract_for(
            error,
            bind_contracts(
                binding
                for declared in event.raises
                if (binding := contract_of(declared)) is not None
            ),
        )
        if binding is not None:
            try:
                rejected = binding.reject(error)
            except Exception:
                logger.exception("RPC event error binding failed for %s", event.name)
                raise RpcReject(
                    RpcRejection.INTERNAL_ERROR, "Internal error"
                ) from error
            if rejected is not None:
                raise rejected from error
        record_error(error)
        rejected = map_rejection(error, rejections)
        if rejected is not None:
            raise rejected from error
        mapped = server.map_error(error, event.name, declared=event.raises)
        if event.on_error == "close":
            rejection = (
                RpcRejection.INTERNAL_ERROR
                if isinstance(mapped, RpcInternalError)
                else RpcRejection.FORBIDDEN
            )
            raise RpcReject(rejection, mapped.message) from error


async def _serve_stream_endpoint(
    endpoint: RpcStreamEndpoint[Any],
    socket: RpcWebSocket,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    limits: RpcLimits | None = None,
    before_accept: RpcBeforeAccept | None = None,
    error_mapper: RpcErrorMapper | None = None,
    rejections: RpcRejections | None = None,
    activity: Activity | None = None,
    middleware: Sequence[RpcMiddlewareLike] = (),
) -> None:
    limits = limits or RpcLimits()
    stream = endpoint.stream
    prepared = await _prepare(
        endpoint,
        socket,
        resolver,
        context,
        stream.path_model,
        before_accept,
        rejections,
    )
    if prepared is None:
        return
    connection, resolved, values, path_values = prepared
    async with _accepted(socket, connection):
        await notify_activity(activity, "connection_opened", connection)
        async with (
            stream_scope(RpcStreamScope(endpoint, connection), middleware)
            if middleware
            else nullcontext()
        ):
            await _run_stream_endpoint(
                endpoint,
                socket,
                connection,
                resolved,
                values,
                path_values,
                limits,
                error_mapper,
                rejections,
                activity,
            )


@asynccontextmanager
async def _accepted(
    socket: RpcWebSocket,
    connection: RpcConnection,
) -> AsyncGenerator[None, None]:
    """Close accepted sockets even when expiry interrupts their initialization."""
    task = asyncio.current_task()
    assert task is not None
    cancelling = task.cancelling()
    connection.track_startup(task)
    try:
        yield
    except asyncio.CancelledError as error:
        if (
            not error.args
            or error.args[0] is not STARTUP_EXPIRED
            or task.cancelling() != cancelling + 1
        ):
            if not connection.closed and connection.close_code is None:
                connection.record_close(RpcConnectionClose.SHUTDOWN)
            raise
        task.uncancel()
    except BaseException as error:
        if not connection.closed and connection.close_code is None:
            connection.record_close(
                RpcConnectionClose.SHUTDOWN
                if isinstance(error, asyncio.CancelledError)
                else RpcConnectionClose.INTERNAL_ERROR
            )
        raise
    finally:
        connection.track_startup(None)
        if not connection.closed:
            with suppress(RpcDisconnect):
                await socket.close(
                    connection.close_code or RpcConnectionClose.NORMAL,
                    connection.close_reason,
                )
            connection.mark_closed()


async def _run_stream_endpoint(
    endpoint: RpcStreamEndpoint[Any],
    socket: RpcWebSocket,
    connection: RpcConnection,
    resolved: RpcResolver,
    values: dict[type[Any], object],
    path_values: dict[str, Any],
    limits: RpcLimits,
    error_mapper: RpcErrorMapper | None,
    rejections: RpcRejections | None,
    activity: Activity | None,
) -> None:
    await _StreamSession(
        endpoint,
        socket,
        connection,
        resolved,
        values,
        path_values,
        limits,
        error_mapper,
        rejections,
        activity,
    ).run()


class _StreamSession:
    def __init__(
        self,
        endpoint: RpcStreamEndpoint[Any],
        socket: RpcWebSocket,
        connection: RpcConnection,
        resolved: RpcResolver,
        values: dict[type[Any], object],
        path_values: dict[str, Any],
        limits: RpcLimits,
        error_mapper: RpcErrorMapper | None,
        rejections: RpcRejections | None,
        activity: Activity | None,
    ) -> None:
        self.endpoint = endpoint
        self.socket = socket
        self.connection = connection
        self.resolved = resolved
        self.values = values
        self.path_values = path_values
        self.limits = limits
        self.error_mapper = error_mapper
        self.rejections = rejections
        self.activity = activity
        self.stream = self.endpoint.stream
        self.close_event = asyncio.Event()
        self.close_value: tuple[RpcConnectionClose, str] = (
            RpcConnectionClose.NORMAL,
            "",
        )
        self.client_closed = False
        self.connection.set_close_handler(self.request_close)
        self.binary_input = (
            RpcBinaryInput.create(self.limits.max_queue_size)
            if self.stream.has_input
            else None
        )
        if not self.stream.is_generator:
            self.values = {
                **self.values,
                RpcBinaryOutput: RpcBinaryOutput.create(
                    self.socket, self.connection, self.activity
                ),
            }
            if self.binary_input is not None:
                self.values[RpcBinaryInput] = self.binary_input
        self.generator: AsyncGenerator[Any, None] | None = None

    async def run(self) -> None:
        try:
            async with (
                connection_scope(self.resolved, self.values) as scoped,
                self.stream.resolver_scope(scoped) as stream_resolver,
            ):
                self.arguments = {
                    p.name: await stream_resolver.resolve(p.dependency)
                    for p in self.stream.injected_parameters
                }
                self.arguments.update(self.path_values)
                if self.stream.is_generator:
                    self.generator = self.stream.function(**self.arguments)
                    run = self._write()
                else:
                    run = self._handle()
                tasks = [asyncio.create_task(run), asyncio.create_task(self._read())]
                try:
                    self.connection.track_startup(None)
                    await self.close_event.wait()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if self.generator is not None:
                        try:
                            await self.generator.aclose()
                        except Exception as error:
                            self._close_stream_error(error)
                        self.generator = None
        except asyncio.CancelledError:
            shutdown = self.connection.close_code or RpcConnectionClose.SHUTDOWN
            self.close_value = (shutdown, self.connection.close_reason)
            self.connection.record_close(shutdown, self.connection.close_reason)
            raise
        except Exception as error:
            self._close_stream_error(error)
        finally:
            if self.generator is not None:
                try:
                    await self.generator.aclose()
                except Exception as error:
                    self._close_stream_error(error)
            if not self.client_closed:
                with suppress(RpcDisconnect):
                    await self.socket.close(*self.close_value)
            self.connection.mark_closed()

    def request_close(
        self, code: RpcConnectionClose, reason: str, raw_close_code: int | None = None
    ) -> None:
        if not self.close_event.is_set() or (
            not self.client_closed
            and self.close_value[0] == RpcConnectionClose.NORMAL
            and (code != RpcConnectionClose.NORMAL)
        ):
            self.close_value = (code, reason)
            self.connection.record_close(code, reason, raw_close_code)
            self.close_event.set()

    def client_disconnected(self, error: RpcDisconnect) -> None:
        self.client_closed = True
        self.request_close(error.code, error.reason, error.raw_close_code)

    def _close_stream_error(self, error: Exception) -> None:
        if isinstance(error, RpcStreamClose):
            self.request_close(error.close, error.reason)
            return
        record_error(error)
        rejected = map_rejection(error, self.rejections)
        if rejected is not None:
            self.request_close(REJECTION_CLOSES[rejected.rejection], rejected.reason)
            return
        try:
            mapped = (
                error
                if isinstance(error, RpcError)
                else self.error_mapper(error)
                if self.error_mapper is not None
                else None
            )
        except Exception:
            logger.exception("RPC binary stream error mapper failed")
            mapped = None
        if mapped is not None:
            self.request_close(
                RpcConnectionClose.POLICY_VIOLATION, f"{mapped.code}: {mapped.message}"
            )
            return
        logger.error("RPC binary stream failed", exc_info=error)
        self.request_close(RpcConnectionClose.INTERNAL_ERROR, "Internal error")

    async def _write(self) -> None:
        try:
            assert self.generator is not None
            async for frame in self.generator:
                if not isinstance(frame, bytes | bytearray | memoryview):
                    record_error(TypeError("Binary stream yielded a non-bytes value"))
                    logger.error("Binary stream yielded a non-bytes value")
                    self.request_close(
                        RpcConnectionClose.INTERNAL_ERROR, "Internal error"
                    )
                    return
                data = bytes(frame)
                await self.socket.send_bytes(data)
                await notify_activity(
                    self.activity, "stream_frame_sent", self.connection, len(data)
                )
            self.request_close(RpcConnectionClose.NORMAL, "")
        except asyncio.CancelledError:
            raise
        except RpcDisconnect as error:
            self.client_disconnected(error)
        except Exception as error:
            self._close_stream_error(error)

    async def _handle(self) -> None:
        try:
            await self.stream.function(**self.arguments)
            self.request_close(RpcConnectionClose.NORMAL, "")
        except asyncio.CancelledError:
            raise
        except RpcDisconnect as error:
            self.client_disconnected(error)
        except Exception as error:
            self._close_stream_error(error)

    async def _read(self) -> None:
        input_ended = False
        try:
            while True:
                frame = await self.socket.receive()
                if self.binary_input is None:
                    self.request_close(
                        RpcConnectionClose.PROTOCOL_ERROR,
                        "Binary stream is server-to-client",
                    )
                    return
                size = len(frame.encode() if isinstance(frame, str) else frame)
                if (
                    self.limits.max_message_bytes is not None
                    and size > self.limits.max_message_bytes
                ):
                    self.request_close(RpcConnectionClose.MESSAGE_TOO_BIG, "")
                    return
                if isinstance(frame, str):
                    if input_ended or not _is_input_end(frame):
                        self.request_close(
                            RpcConnectionClose.PROTOCOL_ERROR,
                            "Unexpected text frame on a binary stream",
                        )
                        return
                    input_ended = True
                    await self.binary_input.end_input()
                elif input_ended:
                    self.request_close(
                        RpcConnectionClose.PROTOCOL_ERROR, "Binary input after end"
                    )
                    return
                else:
                    await self.binary_input.enqueue(frame)
                    await notify_activity(
                        self.activity,
                        "stream_frame_received",
                        self.connection,
                        len(frame),
                    )
        except RpcDisconnect as error:
            self.client_disconnected(error)
        except Exception as error:
            self._close_stream_error(error)


def _is_input_end(frame: str) -> bool:
    try:
        RpcInputEndMessage.model_validate_json(frame)
    except ValidationError:
        return False
    return True


async def _run_socket(
    endpoint: RpcSocketEndpoint[Any],
    socket: RpcWebSocket,
    connection: RpcConnection,
    resolved: RpcResolver,
    values: dict[type[Any], object],
    path_values: dict[str, Any],
    limits: RpcLimits,
    rejections: RpcRejections | None,
    activity: Activity | None,
) -> None:
    await _SocketSession(
        endpoint,
        socket,
        connection,
        resolved,
        values,
        path_values,
        limits,
        rejections,
        activity,
    ).run()


class _SocketSession:
    def __init__(
        self,
        endpoint: RpcSocketEndpoint[Any],
        socket: RpcWebSocket,
        connection: RpcConnection,
        resolved: RpcResolver,
        values: dict[type[Any], object],
        path_values: dict[str, Any],
        limits: RpcLimits,
        rejections: RpcRejections | None,
        activity: Activity | None,
    ) -> None:
        self.endpoint = endpoint
        self.socket = socket
        self.connection = connection
        self.resolved = resolved
        self.values = values
        self.path_values = path_values
        self.limits = limits
        self.rejections = rejections
        self.activity = activity
        self.definition = self.endpoint.socket
        self.closes = bind_contracts(self.definition.closes)
        self.close_event = asyncio.Event()
        self.close_value: tuple[RpcConnectionClose, str] = (
            RpcConnectionClose.NORMAL,
            "",
        )
        self.client_closed = False
        self.connection.set_close_handler(self.request_close)
        self.socket_handle = RpcSocket.create(
            self.socket,
            self.connection,
            self.definition.messages,
            self.definition.protocol,
            self.definition.message_contract,
            self.limits,
            self.activity,
            self.request_close,
            self.client_disconnected,
        )
        self.values = {**self.values, RpcSocket: self.socket_handle}

    async def run(self) -> None:
        try:
            async with (
                connection_scope(self.resolved, self.values) as self.scoped,
                self.definition.resolver_scope(self.scoped)
                if self.definition.message_parameter is None
                else nullcontext(self.scoped) as socket_resolver,
            ):
                self.arguments = (
                    {
                        p.name: await socket_resolver.resolve(p.dependency)
                        for p in self.definition.injected_parameters
                    }
                    if self.definition.message_parameter is None
                    else {}
                )
                self.arguments.update(self.path_values)

                tasks = [
                    asyncio.create_task(self._handle()),
                    asyncio.create_task(self._read()),
                ]
                try:
                    self.connection.track_startup(None)
                    await self.close_event.wait()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            shutdown = self.connection.close_code or RpcConnectionClose.SHUTDOWN
            self.close_value = (shutdown, self.connection.close_reason)
            self.connection.record_close(shutdown, self.connection.close_reason)
            raise
        except Exception as error:
            self._close_failure(error)
        finally:
            if not self.client_closed:
                with suppress(RpcDisconnect):
                    await self.socket.close(*self.close_value)
            self.connection.mark_closed()

    def request_close(
        self, code: RpcConnectionClose, reason: str, raw_close_code: int | None = None
    ) -> None:
        if not self.close_event.is_set() or (
            not self.client_closed
            and self.close_value[0] == RpcConnectionClose.NORMAL
            and (code != RpcConnectionClose.NORMAL)
        ):
            self.close_value = (code, reason)
            self.connection.record_close(code, reason, raw_close_code)
            self.close_event.set()

    def client_disconnected(self, error: RpcDisconnect) -> None:
        if not self.client_closed:
            self.client_closed = True
            self.request_close(error.code, error.reason, error.raw_close_code)

    def _close_failure(self, error: Exception) -> None:
        leaves = _leaf_exceptions(error)
        failure = next((e for e in leaves if not isinstance(e, RpcDisconnect)), None)
        if failure is None:
            self.request_close(RpcConnectionClose.NORMAL, "")
            return
        record_error(failure)
        rejected = None
        binding = contract_for(failure, self.closes)
        if binding is not None:
            try:
                rejected = binding.reject(failure)
            except Exception:
                logger.exception("RPC socket close binding failed")
        if rejected is None:
            rejected = map_rejection(failure, self.rejections)
        if rejected is not None:
            self.request_close(REJECTION_CLOSES[rejected.rejection], rejected.reason)
            return
        logger.error("RPC socket %s failed", self.definition.name, exc_info=failure)
        self.request_close(RpcConnectionClose.INTERNAL_ERROR, "Internal error")

    async def _respond(self, message: object) -> None:
        async with self.definition.resolver_scope(self.scoped) as message_resolver:
            message_arguments = {
                p.name: await message_resolver.resolve(p.dependency)
                for p in self.definition.injected_parameters
            }
            message_arguments.update(self.path_values)
            assert self.definition.message_parameter is not None
            message_arguments[self.definition.message_parameter] = message
            response = await self.definition.function(**message_arguments)
            await self.socket_handle.send(response)

    async def _handle(self) -> None:
        try:
            if self.definition.message_parameter is None:
                await self.definition.function(**self.arguments)
            else:
                async for frame in self.socket_handle:
                    await self._respond(frame)
            self.request_close(RpcConnectionClose.NORMAL, "")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._close_failure(error)

    async def _read(self) -> None:
        try:
            while True:
                message = await self.socket.receive()
                size = len(message.encode() if isinstance(message, str) else message)
                if (
                    self.limits.max_message_bytes is not None
                    and size > self.limits.max_message_bytes
                ):
                    self.request_close(RpcConnectionClose.MESSAGE_TOO_BIG, "")
                    return
                message_type = (
                    RpcMessageType.TEXT
                    if isinstance(message, str)
                    else RpcMessageType.BINARY
                )
                if message_type not in self.definition.messages:
                    self.request_close(
                        RpcConnectionClose.PROTOCOL_ERROR,
                        f"Unsupported {message_type} message",
                    )
                    return
                if not await self.socket_handle.enqueue(message):
                    return
                await notify_activity(
                    self.activity, "stream_frame_received", self.connection, size
                )
        except RpcDisconnect as error:
            self.client_disconnected(error)
        except Exception as error:
            record_error(error)
            logger.exception("RPC socket reader failed")
            self.request_close(RpcConnectionClose.INTERNAL_ERROR, "Internal error")


def _leaf_exceptions(error: Exception) -> list[Exception]:
    if isinstance(error, ExceptionGroup):
        return [leaf for item in error.exceptions for leaf in _leaf_exceptions(item)]
    return [error]
