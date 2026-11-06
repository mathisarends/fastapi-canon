import subprocess
import sys

import pytest

from fastapi_canon import APIRouter
from fastapi_canon.exceptions import ConfigurationError


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
