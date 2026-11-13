from dataclasses import replace

import pytest
from fastapi_canon.integrations.websockets import RpcConnection, RpcHandshake
from fastapi_canon.integrations.websockets.testing import InMemorySocket


@pytest.mark.parametrize("path_params", [None, {}])
def test_explicit_empty_path_parameters_override_the_handshake(
    path_params: dict[str, str] | None,
) -> None:
    class Socket(InMemorySocket):
        @property
        def handshake(self) -> RpcHandshake:
            return replace(super().handshake, path_params={"room": "one"})

    connection = RpcConnection.create("room", Socket("/rooms/one"), path_params)

    assert connection.path_params == ({"room": "one"} if path_params is None else {})
