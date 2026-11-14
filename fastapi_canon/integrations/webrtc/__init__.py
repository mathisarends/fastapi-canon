"""Typed WebRTC sessions for FastAPI.

Declare sessions with ``WebRtcRouter.session()``; Canon provides WebSocket signaling,
admission through FastAPI dependencies, limits and cleanup, while an engine
adapter (see :mod:`fastapi_canon.integrations.webrtc.engines`) owns the peer
connection.
"""

from fastapi_canon.integrations.webrtc.canon import WebRtcRouter
from fastapi_canon.integrations.webrtc.capacity import SessionCapacity
from fastapi_canon.integrations.webrtc.declarations import (
    DataChannelSpec,
    Direction,
    MediaPolicy,
    MessageFormat,
    Role,
    Session,
    SessionContext,
    SessionLimits,
)
from fastapi_canon.integrations.webrtc.engine import MediaKind
from fastapi_canon.integrations.webrtc.errors import (
    ErrorCode,
    SessionClosedError,
    WebRtcError,
)
from fastapi_canon.integrations.webrtc.events import (
    EventHook,
    SessionEvent,
    SessionEventKind,
)
from fastapi_canon.integrations.webrtc.ice import (
    IceServer,
    IceServers,
    IceServerSource,
    TurnRestCredentials,
)
from fastapi_canon.integrations.webrtc.peer import (
    Channel,
    IncomingTrack,
    OutgoingTrack,
    Peer,
)
from fastapi_canon.integrations.webrtc.protocol import PROTOCOL_VERSION, SUBPROTOCOL

__all__ = [
    "PROTOCOL_VERSION",
    "SUBPROTOCOL",
    "Channel",
    "DataChannelSpec",
    "Direction",
    "ErrorCode",
    "EventHook",
    "IceServer",
    "IceServerSource",
    "IceServers",
    "IncomingTrack",
    "MediaKind",
    "MediaPolicy",
    "MessageFormat",
    "OutgoingTrack",
    "Peer",
    "Role",
    "Session",
    "SessionCapacity",
    "SessionClosedError",
    "SessionContext",
    "SessionEvent",
    "SessionEventKind",
    "SessionLimits",
    "TurnRestCredentials",
    "WebRtcError",
    "WebRtcRouter",
]
