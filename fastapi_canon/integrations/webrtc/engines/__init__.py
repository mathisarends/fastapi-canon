"""Types for writing WebRTC engine adapters.

Concrete adapters live in submodules, for example
``fastapi_canon.integrations.webrtc.engines.aiortc``; importing this package loads
no media engine.
"""

from fastapi_canon.integrations.webrtc.engine import (
    CandidatePairStats,
    ChannelState,
    ConnectionState,
    ConnectionStats,
    DescriptionType,
    Engine,
    EngineCapabilities,
    MediaStats,
    SignalingState,
    TransceiverDirection,
)

__all__ = [
    "CandidatePairStats",
    "ChannelState",
    "ConnectionState",
    "ConnectionStats",
    "DescriptionType",
    "Engine",
    "EngineCapabilities",
    "MediaStats",
    "SignalingState",
    "TransceiverDirection",
]
