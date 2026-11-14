"""Resource instance ownership and app-wide resource instrumentation providers."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib.metadata import entry_points
from threading import Lock
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from fastapi_canon.exceptions import FeatureConfigurationError

if TYPE_CHECKING:
    from dishka import Provider

    from fastapi_canon.integrations.opentelemetry.declarations import (
        ResourceInstrumentation,
        Telemetry,
    )

RESOURCE_GROUP = "fastapi_canon.telemetry_resources"

_OWNERS: WeakKeyDictionary[object, bool] = WeakKeyDictionary()
_LOCK = Lock()


@contextmanager
def claim_resource(resource: object) -> Iterator[None]:
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


def is_claimed(resource: object) -> bool:
    with _LOCK:
        return resource in _OWNERS


def default_resources() -> tuple[ResourceInstrumentation, ...]:
    """Create every installed adapter; skip those missing optional packages."""
    adapters = []
    for entry in sorted(entry_points(group=RESOURCE_GROUP), key=lambda item: item.name):
        try:
            adapters.append(entry.load()())
        except ModuleNotFoundError:
            continue
    return tuple(adapters)


def resource_providers(
    providers: tuple[Provider, ...],
    adapters: Sequence[ResourceInstrumentation],
    telemetry: Telemetry,
) -> tuple[Provider, ...]:
    """Decorate each provided class that an adapter accepts, per component."""
    from dishka import Provider

    targets: dict[tuple[str, type[Any]], ResourceInstrumentation] = {}
    for provider in providers:
        for factory in provider.factories:
            provided = factory.provides.type_hint
            if not isinstance(provided, type):
                continue
            adapter = next((item for item in adapters if item.accepts(provided)), None)
            if adapter is not None:
                targets.setdefault((provider.component or "", provided), adapter)
    decorators = []
    for (component, provided), adapter in targets.items():
        decorator = Provider(component=component)
        decorator.decorate(_decorator(provided, adapter, telemetry), provides=provided)
        decorators.append(decorator)
    return tuple(decorators)


def _decorator(
    provided: type[Any], adapter: ResourceInstrumentation, telemetry: Telemetry
) -> Any:
    def instrument(resource: Any) -> Iterator[Any]:
        with adapter.instrument(resource, telemetry):
            yield resource

    instrument.__annotations__ = {"resource": provided, "return": Iterator[provided]}  # type: ignore[valid-type]
    return instrument
