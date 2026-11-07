"""Shared session capacity accounting."""

import pytest
from fastapi_canon.integrations.webrtc import SessionCapacity
from fastapi_canon.integrations.webrtc.capacity import SlotReservation


@pytest.mark.parametrize("max_sessions", [0, -1, True, 1.5])
def test_capacity_rejects_non_positive_limits(max_sessions: object) -> None:
    with pytest.raises(ValueError, match="max_sessions must be positive"):
        SessionCapacity(max_sessions=max_sessions)  # type: ignore[arg-type]


def test_reservation_holds_one_slot_in_every_capacity_until_released() -> None:
    shared = SessionCapacity(max_sessions=1)
    local = SessionCapacity(max_sessions=2)

    reservation = SlotReservation(shared, local)

    assert (shared.active, shared.full) == (1, True)
    assert (local.active, local.full) == (1, False)
    reservation.release()
    reservation.release()
    assert (shared.active, local.active) == (0, 0)
    assert repr(shared) == "SessionCapacity(max_sessions=1, active=0)"
