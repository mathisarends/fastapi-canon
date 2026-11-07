"""Types for writing WebRTC engine adapters.

An adapter implements :class:`Engine` and :class:`EnginePeer` for one WebRTC
stack. Concrete adapters live in submodules, for example
``fastapi_canon.integrations.webrtc.engines.aiortc``; importing this package loads
no media engine. ``MediaKind`` is exported by ``fastapi_canon.integrations.webrtc``.
"""

from fastapi_canon.integrations.webrtc.engine import (
    CandidatePairStats,
    ChannelParameters,
    ChannelState,
    ConnectionState,
    ConnectionStats,
    DescriptionType,
    Engine,
    EngineCapabilities,
    EngineDataChannel,
    EnginePeer,
    EngineSender,
    EngineTrack,
    IceCandidate,
    MediaStats,
    PeerConfig,
    PeerObserver,
    SessionDescription,
    SignalingState,
    TransceiverDirection,
)

__all__ = [
    "CandidatePairStats",
    "ChannelParameters",
    "ChannelState",
    "ConnectionState",
    "ConnectionStats",
    "DescriptionType",
    "Engine",
    "EngineCapabilities",
    "EngineDataChannel",
    "EnginePeer",
    "EngineSender",
    "EngineTrack",
    "IceCandidate",
    "MediaStats",
    "PeerConfig",
    "PeerObserver",
    "SessionDescription",
    "SignalingState",
    "TransceiverDirection",
]
