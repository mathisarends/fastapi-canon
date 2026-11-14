"""Optional telemetry declarations; importing this module needs no OTEL packages."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from starlette.types import Scope as ASGIScope

from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integration import Installer, Integration, IntegrationContext

if TYPE_CHECKING:
    from dishka import Provider
    from opentelemetry._logs import LoggerProvider
    from opentelemetry.metrics import MeterProvider
    from opentelemetry.trace import TracerProvider

    from fastapi_canon.integrations.websockets.adapters.opentelemetry import (
        OpenTelemetry,
    )

__all__ = [
    "Instrumentation",
    "Telemetry",
    "instrument",
]


@dataclass(frozen=True, slots=True, init=False)
class Instrumentation:
    """An immutable instrumentation declaration for ports, given to ``Telemetry``.

    Create it with ``instrument``; one declaration is one feature integration part.
    """

    integration: ClassVar[type[Integration]]
    ports: tuple[type[Any], ...]
    expected: tuple[type[Exception], ...]
    component: str

    def __init__(
        self,
        *ports: type[Any],
        expected: Sequence[type[Exception]] = (),
        component: str = "",
    ) -> None:
        if not ports:
            raise FeatureConfigurationError("instrument requires at least one port")
        for port in ports:
            if not isinstance(port, type):
                raise FeatureConfigurationError("instrument port must be a class")
            if not instrumented_methods(port):
                raise FeatureConfigurationError(
                    f"{port.__name__}: instrument requires public async methods or async __call__"
                )
        if len(set(ports)) != len(ports):
            raise FeatureConfigurationError("instrument ports must be unique")
        failures = tuple(expected)
        if any(
            not isinstance(cls, type) or not issubclass(cls, Exception)
            for cls in failures
        ):
            raise FeatureConfigurationError(
                "expected must contain Exception subclasses"
            )
        if not isinstance(component, str):
            raise FeatureConfigurationError("component must be a string")
        object.__setattr__(self, "ports", ports)
        object.__setattr__(self, "expected", failures)
        object.__setattr__(self, "component", component)


def instrument(
    port: type[Any],
    /,
    *more_ports: type[Any],
    expected: Sequence[type[Exception]] = (),
    component: str = "",
) -> Instrumentation:
    """Declare instrumentation for one or more ports or async callable use cases.

    Pass the result to ``Feature(integrations=[instrument(Port, ...)])``. Shared
    expected failures and the Dishka component apply to every port; use one
    call per differently configured group. The composition must enable
    ``Telemetry``; no OpenTelemetry import or Dishka decorator is created before
    it composes. The default component is Dishka's empty component.
    """
    return Instrumentation(port, *more_ports, expected=expected, component=component)


@dataclass(frozen=True, slots=True, kw_only=True)
class Telemetry(Integration):
    """Use application-owned providers, with FastAPI automatic exporters disabled.

    Enable it once with ``Composition(integrations=[Telemetry(...)])``. Every
    Dishka component of the composition then binds this configuration, and
    features instrument ports with ``instrument``.
    """

    exclude: Callable[[ASGIScope], bool] | None = None
    operation_spans: bool = True
    port_spans: bool = True
    port_metrics: bool = False
    problem_details: bool = True
    tracing: bool = True
    metrics: bool = True
    logs: bool = True
    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    logger_provider: LoggerProvider | None = None

    def __post_init__(self) -> None:
        for name in (
            "operation_spans",
            "port_spans",
            "port_metrics",
            "problem_details",
            "tracing",
            "metrics",
            "logs",
        ):
            if not isinstance(getattr(self, name), bool):
                raise FeatureConfigurationError(f"{name} must be a bool")
        if self.exclude is not None and not callable(self.exclude):
            raise FeatureConfigurationError("exclude must be callable or None")

    @classmethod
    def disabled(cls) -> Telemetry:
        """Return configuration for uninstrumented infrastructure, without OTEL imports."""
        return cls(tracing=False, metrics=False, logs=False, port_spans=False)

    def provider(self, *, component: str = "") -> Provider:
        """Bind this borrowed configuration in a standalone Dishka container."""
        from dishka import Provider, Scope

        provider = Provider(component=component)
        provider.provide(lambda: self, provides=Telemetry, scope=Scope.APP)
        return provider

    def websocket_middleware(self) -> OpenTelemetry:
        """Create the WebSocket middleware explicitly, forwarding the same providers.

        Install ``fastapi-canon[websockets,otel]`` and pass this to
        ``CanonWebSocketRouter``. Calls without message context start independent
        traces. No connection span is added, and remote baggage remains disabled.
        """
        from fastapi_canon.integrations.websockets.adapters.opentelemetry import (
            OpenTelemetry,
        )

        return OpenTelemetry(
            tracing=self.tracing,
            metrics=self.metrics,
            tracer_provider=self.tracer_provider,
            meter_provider=self.meter_provider,
        )

    def providers(
        self,
        parts: tuple[tuple[str, Instrumentation], ...],
        providers: tuple[Provider, ...],
    ) -> tuple[Provider, ...]:
        """Bind this configuration per component and decorate instrumented ports."""
        _reject_reserved_binding(providers)
        instrumentation = instrumentation_providers(parts, self)
        if not providers and not instrumentation:
            return ()
        components = dict.fromkeys(
            provider.component for provider in (*providers, *instrumentation)
        )
        return (
            *instrumentation,
            *(self.provider(component=component) for component in components),
        )

    def prepare(self, context: IntegrationContext) -> Installer | None:
        """Check FastAPI's native telemetry support; return the outer installer.

        A configuration that records nothing, such as ``Telemetry.disabled()``,
        installs nothing and imports no OpenTelemetry package.
        """
        if not (self.tracing or self.metrics or self.logs or self.port_metrics):
            return None
        try:
            # lazy telemetry adapter factory
            from fastapi_canon.integrations.opentelemetry.native import (  # smelt: ignore[SMT104] -- factory cycle
                install,
                validate_app,
            )
        except ModuleNotFoundError as exc:
            if exc.name is None or not exc.name.startswith("opentelemetry"):
                raise
            raise ModuleNotFoundError(
                "Canon telemetry requires OpenTelemetry; install fastapi-canon[otel]",
                name=exc.name,
            ) from exc

        validate_app(context.app)
        return Installer(lambda app, container: install(app, self), before_routes=True)


Instrumentation.integration = Telemetry


def instrumentation_providers(
    registrations: tuple[tuple[str, Instrumentation], ...],
    options: Telemetry,
) -> tuple[Provider, ...]:
    ports = tuple(
        (feature, port, spec) for feature, spec in registrations for port in spec.ports
    )
    seen: dict[tuple[str, type[Any]], str] = {}
    for feature, port, spec in ports:
        key = (spec.component, port)
        if key in seen:
            raise FeatureConfigurationError(
                f"port {port.__name__} is instrumented more than once in "
                f"component {spec.component!r} by features {seen[key]!r} and {feature!r}"
            )
        seen[key] = feature
    if not ports or not (
        (options.tracing and options.port_spans) or options.port_metrics
    ):
        return ()
    from fastapi_canon.integrations.opentelemetry.ports import (
        create_instrumentation_providers,
    )

    return create_instrumentation_providers(ports, options)


def instrumented_methods(
    port: type[Any],
) -> tuple[tuple[str, Callable[..., Any]], ...]:
    return tuple(
        (name, method)
        for name, method in inspect.getmembers(port, inspect.iscoroutinefunction)
        if name == "__call__" or not name.startswith("_")
    )


def _reject_reserved_binding(providers: tuple[Provider, ...]) -> None:
    for provider in providers:
        if (
            any(
                factory.provides.type_hint is Telemetry
                for factory in provider.factories
            )
            or any(alias.provides.type_hint is Telemetry for alias in provider.aliases)
            or any(
                variable.provides.type_hint is Telemetry
                for variable in provider.context_vars
            )
        ):
            raise FeatureConfigurationError(
                "Telemetry is reserved by Composition; remove the custom binding"
            )
