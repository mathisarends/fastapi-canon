"""Lifecycle events for metrics and audit hooks.

Events carry identifiers, categories and timings only. They never contain SDP,
ICE candidates, addresses, credentials or message payloads.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from fastapi_canon.integrations.webrtc.engine import MediaKind
from fastapi_canon.integrations.webrtc.errors import ErrorCode


class SessionEventKind(StrEnum):
    ADMITTED = "admitted"
    REJECTED = "rejected"
    CONNECTED = "connected"
    READY = "ready"
    MEDIA_READY = "media_ready"
    TRACK = "track"
    RENEGOTIATED = "renegotiated"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """One step in a session's lifecycle.

    * ``admitted``: dependencies succeeded and a session slot was reserved.
    * ``rejected``: the session was refused after admission (``code`` says why).
    * ``connected``: the transport connected and every channel opened.
    * ``ready``: ``on_start`` finished; message handlers and ``run`` started.
    * ``media_ready``: every expected incoming media kind delivered packets.
    * ``track``: an incoming track arrived (``media_kind`` is set).
    * ``renegotiated``: a later offer/answer exchange completed.
    * ``closed``: cleanup finished (``code`` is the termination category).
    """

    kind: SessionEventKind
    name: str
    session_id: str
    elapsed_seconds: float
    """Seconds since admission."""
    code: ErrorCode | None = None
    media_kind: MediaKind | None = None
    attributes: Mapping[str, str | int | float | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", _EventAttributes(self.attributes))


type EventHook = Callable[[SessionEvent], None]
"""A synchronous, non-blocking observer. Exceptions are logged and ignored."""


class _EventAttributes(Mapping[str, str | int | float | bool]):
    def __init__(self, attributes: Mapping[str, str | int | float | bool]) -> None:
        self._values = MappingProxyType(dict(attributes))

    def __getitem__(self, key: str) -> str | int | float | bool:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, str | int | float | bool]:
        # dataclasses.asdict keeps its existing JSON-compatible dictionary output.
        return dict(self._values)
