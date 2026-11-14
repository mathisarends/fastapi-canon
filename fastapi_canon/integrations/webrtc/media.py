import contextlib
from typing import Any


def stop_native_track(native: Any) -> None:
    stop = getattr(native, "stop", None)
    if callable(stop):
        with contextlib.suppress(Exception):
            stop()
