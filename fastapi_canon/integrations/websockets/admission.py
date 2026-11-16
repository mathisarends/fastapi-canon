import asyncio
from collections.abc import Coroutine
from typing import Any

from fastapi_canon.integrations.websockets.envelopes import (
    RpcFailure,
    RpcRequestEnvelope,
    RpcSuccess,
)


class PendingCall:
    def __init__(
        self,
        *,
        message: Any,
        reservation: object | None,
        invalid: bool,
        batch: bool,
        admitted_at: float,
        shutting_down: bool = False,
        envelope: RpcRequestEnvelope | None = None,
    ) -> None:
        self.message = message
        self.reservation = reservation
        self.invalid = invalid
        self.batch = batch
        self.admitted_at = admitted_at
        self.shutting_down = shutting_down
        self.envelope = envelope
        self._task: asyncio.Task[RpcSuccess | RpcFailure | None] | None = None
        self._started = False
        self._cancel_requested = False

    @property
    def task(self) -> asyncio.Task[RpcSuccess | RpcFailure | None]:
        if self._task is None:
            raise RuntimeError("Pending call has not been scheduled")
        return self._task

    @property
    def started(self) -> bool:
        return self._started

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def schedule(
        self, operation: Coroutine[Any, Any, RpcSuccess | RpcFailure | None]
    ) -> None:
        self._task = asyncio.create_task(operation)

    def start(self) -> None:
        self._started = True

    def cancel(self, reason: object) -> None:
        self._cancel_requested = True
        self.task.cancel(reason)


class PendingRequests:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._reservations: set[object] = set()

    @property
    def count(self) -> int:
        return len(self._reservations)

    def reserve(self) -> object | None:
        if self.count >= self._limit:
            return None
        reservation = object()
        self._reservations.add(reservation)
        return reservation

    def release(self, reservation: object) -> None:
        self._reservations.discard(reservation)
