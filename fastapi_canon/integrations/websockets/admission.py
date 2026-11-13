import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi_canon.integrations.websockets.envelopes import (
    RpcFailure,
    RpcRequestEnvelope,
    RpcSuccess,
)


@dataclass
class PendingCall:
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
