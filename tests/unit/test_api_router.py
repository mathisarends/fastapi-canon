import inspect
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import APIRouter as FastAPIRouter

from fastapi_canon import APIRouter
from fastapi_canon.api_router import ChannelOptions, SessionOptions, StreamOptions
from fastapi_canon.exceptions import ConfigurationError
from fastapi_canon.integrations.sse import CanonStreamRouter
from fastapi_canon.integrations.webrtc import CanonWebRtcRouter
from fastapi_canon.integrations.websockets import CanonWebSocketRouter


def test_core_router_import_keeps_optional_transports_lazy() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from fastapi_canon import APIRouter; "
            "router = APIRouter(); "
            "assert 'fastapi_canon.integrations.sse.canon' not in sys.modules; "
            "assert 'fastapi_canon.integrations.websockets' not in sys.modules; "
            "assert 'aiortc' not in sys.modules; "
            "assert 'jinja2' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_channel_does_not_change_routes() -> None:
    router = APIRouter()
    with pytest.raises(ConfigurationError, match="RpcChannel"):
        router.include_channel(object(), path="/session")  # type: ignore[arg-type]
    assert router.routes == []


def test_channel_options_match_the_websocket_adapter() -> None:
    accepted = _keywords(CanonWebSocketRouter.__init__) - {"path", "channels", "prefix"}

    assert set(ChannelOptions.__annotations__) == accepted


def test_session_options_match_the_webrtc_adapter() -> None:
    # Canon derives admission errors from raises=.
    accepted = _keywords(CanonWebRtcRouter.session) - {"path", "admission_errors"}

    assert set(SessionOptions.__annotations__) == accepted


def test_stream_options_are_route_options() -> None:
    # Canon's api_route forwards the remaining options to FastAPI's.
    accepted = _keywords(CanonStreamRouter.api_route) | _keywords(
        FastAPIRouter.api_route
    )

    assert set(StreamOptions.__annotations__) <= accepted


def _keywords(function: Callable[..., Any]) -> set[str]:
    parameters = inspect.signature(function).parameters.values()
    return {
        parameter.name
        for parameter in parameters
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        or (
            parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            and parameter.name != "self"
        )
    }
