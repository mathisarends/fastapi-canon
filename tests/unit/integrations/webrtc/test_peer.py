import asyncio

import pytest
from fastapi_canon.integrations.webrtc import IncomingTrack

from tests.unit.integrations.webrtc.fake_engine import FakeTrack


@pytest.mark.parametrize("already_ended", [False, True])
async def test_stop_releases_incoming_media_and_ends_iteration(
    already_ended: bool,
) -> None:
    native = FakeTrack()
    track = IncomingTrack(native)
    if already_ended:
        native.end()

    track.stop()

    assert native.stopped
    assert track.ended
    async with asyncio.timeout(1):
        await track.wait_ended()
        with pytest.raises(StopAsyncIteration):
            await anext(aiter(track))
