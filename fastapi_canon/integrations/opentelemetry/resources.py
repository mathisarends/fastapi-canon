"""Exclusive lifetime ownership of explicitly instrumented resource instances."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from weakref import WeakKeyDictionary

from fastapi_canon.error.types import FeatureConfigurationError

_OWNERS: WeakKeyDictionary[object, bool] = WeakKeyDictionary()
_LOCK = Lock()


@contextmanager
def _claim(resource: object) -> Iterator[None]:
    with _LOCK:
        if resource in _OWNERS:
            raise FeatureConfigurationError(
                "this resource already has Canon instrumentation"
            )
        _OWNERS[resource] = True
    try:
        yield
    finally:
        with _LOCK:
            del _OWNERS[resource]
