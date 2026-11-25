import inspect
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import APIRouter as FastAPIRouter

from fastapi_canon import (
    APIRouteOptions,
    APIRouter,
    ChannelOptions,
    IncludeRouterOptions,
    RouteOptions,
)
from fastapi_canon.errors import ConfigurationError
from fastapi_canon.integrations.websockets import RpcChannel, WebSocketPolicy
from fastapi_canon.integrations.websockets.adapters.fastapi import CanonWebSocketRouter
from fastapi_canon.routing.http import FastAPIRouterOptions


def test_core_router_import_keeps_optional_transports_lazy() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from fastapi_canon import APIRouter, APIRouteOptions, ChannelOptions, IncludeRouterOptions, RouteOptions; "
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


def test_websocket_policy_must_be_a_policy() -> None:
    with pytest.raises(ConfigurationError, match="WebSocketPolicy"):
        APIRouter(websocket_policy=object())  # type: ignore[arg-type]


def test_declarations_conflicting_with_the_policy_fail_before_registration() -> None:
    router = APIRouter(websocket_policy=WebSocketPolicy(strict_errors=True))

    with pytest.raises(ConfigurationError, match="strict_errors"):
        router.socket("/socket", strict_errors=False)
    with pytest.raises(ConfigurationError, match="strict_errors"):
        router.include_channel(RpcChannel(), path="/session", strict_errors=False)
    assert router.routes == []


@pytest.mark.parametrize(
    "options,function",
    [
        (FastAPIRouterOptions, FastAPIRouter.__init__),
        (IncludeRouterOptions, FastAPIRouter.include_router),
        (RouteOptions, FastAPIRouter.get),
        (APIRouteOptions, FastAPIRouter.api_route),
    ],
)
def test_http_options_match_fastapi(
    options: type, function: Callable[..., Any]
) -> None:
    # The keyword surface is intentionally explicit; additions in a supported
    # FastAPI version must be reviewed rather than silently accepted as Any.
    assert set(options.__annotations__) == _keywords(function) - {"path", "router"}


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
