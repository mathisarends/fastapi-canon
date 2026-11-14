import pytest
from fastapi_canon.integrations.webrtc import ErrorCode, WebRtcError
from fastapi_canon.integrations.webrtc.errors import default_message


@pytest.mark.parametrize("message", [None, ""])
def test_only_none_selects_the_default_error_message(message: str | None) -> None:
    error = WebRtcError(ErrorCode.INVALID_MESSAGE, message)

    assert error.message == (default_message(error.code) if message is None else "")
