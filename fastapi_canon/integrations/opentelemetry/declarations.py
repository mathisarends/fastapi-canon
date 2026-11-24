from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    NamedTuple,
    Protocol,
    runtime_checkable,
)

from starlette.types import Scope as ASGIScope

from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integration import (
    ErrorLookup,
    Installer,
    Integration,
    IntegrationContext,
    ProviderContext,
)

if TYPE_CHECKING:
    from dishka import Provider
    from opentelemetry._logs import LoggerProvider
    from opentelemetry.metrics import MeterProvider
    from opentelemetry.trace import TracerProvider

    from fastapi_canon.integrations.websockets.adapters.opentelemetry import (
        OpenTelemetry,
    )


@runtime_checkable
class ResourceInstrumentation(Protocol):
    """Instrument every Dishka-provided resource of an accepted type.

    Register a zero-argument factory in the ``fastapi_canon.telemetry_resources``
    entry-point group to enable it by default. ``instrument`` returns a context
    that stays open for the resource's Dishka lifetime; it must not instrument
    a resource that already has instrumentation.
    """

    def accepts(self, provided: type[Any]) -> bool:
        """Return whether a provided type should be instrumented."""
        ...

    def instrument(
        self, resource: Any, telemetry: Telemetry
    ) -> AbstractContextManager[object]:
        """Return the context that instruments *resource* while it is open."""
        ...


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
class SdkProviders:
    """The providers a ``Telemetry(sdk=...)`` factory yields.

    A provider left as ``None`` keeps using the current global provider.
    """

    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    logger_provider: LoggerProvider | None = None


type SdkFactory = Callable[[], AbstractContextManager[SdkProviders]]
"""Create one container's SDK providers on entry; release them on exit."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Telemetry(Integration):
    """Configure telemetry, with FastAPI automatic exporters disabled.

    Enable it once with ``Composition(integrations=[Telemetry(...)])``. Every
    Dishka component of the composition then binds this configuration, and
    features instrument ports with ``instrument``. Pass ``sdk`` to let each
    application own its SDK providers, or explicit providers to borrow them.
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
    resources: Sequence[ResourceInstrumentation] | None = None
    shutdown: Callable[[], None] | None = None
    sdk: SdkFactory | None = None

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
        for name in ("exclude", "shutdown", "sdk"):
            value = getattr(self, name)
            if value is not None and not callable(value):
                raise FeatureConfigurationError(f"{name} must be callable or None")
        _reject_borrowed_with_sdk(self)
        if self.resources is not None:
            resources = tuple(self.resources)
            if not all(isinstance(item, ResourceInstrumentation) for item in resources):
                raise FeatureConfigurationError(
                    "resources must contain ResourceInstrumentation instances"
                )
            object.__setattr__(self, "resources", resources)

    @classmethod
    def disabled(cls) -> Telemetry:
        """Return configuration for uninstrumented infrastructure, without OTEL imports."""
        return cls(tracing=False, metrics=False, logs=False, port_spans=False)

    def provider(self, *, component: str = "") -> Provider:
        """Bind this configuration in a standalone Dishka container.

        With ``sdk``, the default component's provider enters the factory once
        per container, on first use, and exits it when the container closes.
        Other components share those SDK providers.
        """
        from dishka import Provider, Scope

        provider = Provider(component=component)
        if self.sdk is None:
            provider.provide(lambda: self, provides=Telemetry, scope=Scope.APP)
            return provider
        if component == "":
            provider.provide(
                _sdk_resource(self.sdk), provides=SdkProviders, scope=Scope.APP
            )
        provider.provide(_owned(self), provides=Telemetry, scope=Scope.APP)
        return provider

    def websocket_middleware(self) -> OpenTelemetry:
        """Create the WebSocket middleware explicitly, forwarding the same providers.

        Install ``fastapi-canon[websockets,otel]`` and pass this to
        ``CanonWebSocketRouter``. Calls without message context start independent
        traces. No connection span is added, and remote baggage remains disabled.
        Providers owned through ``sdk`` do not exist yet, so ``sdk`` is rejected.
        """
        if self.sdk is not None:
            raise FeatureConfigurationError(
                "websocket_middleware() needs explicit providers; "
                "Telemetry(sdk=...) creates them only when the app starts"
            )
        from fastapi_canon.integrations.websockets.adapters.opentelemetry import (
            OpenTelemetry,
        )

        return OpenTelemetry(
            tracing=self.tracing,
            metrics=self.metrics,
            tracer_provider=self.tracer_provider,
            meter_provider=self.meter_provider,
        )

    def finalizer(self) -> Callable[[], None] | None:
        """Return the application-owned SDK shutdown callable."""
        return self.shutdown

    def providers(self, context: ProviderContext) -> tuple[Provider, ...]:
        """Decorate the declared ports."""
        return instrumentation_providers(context.parts, self, context.errors)

    def final_providers(self, context: ProviderContext) -> tuple[Provider, ...]:
        """Bind this configuration per component; decorate effective resources.

        ``resources=None`` uses every installed ``fastapi_canon.telemetry_resources``
        entry point; ``resources=[]`` instruments no resource.
        """
        _reject_reserved_binding(context.providers)
        resources = self._resource_providers(context.providers)
        components = dict.fromkeys(
            (
                *([""] if self.sdk is not None else []),
                *(provider.component for provider in (*context.providers, *resources)),
            )
        )
        return (
            *resources,
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
            from fastapi_canon.integrations.opentelemetry.native import (  # smelt: ignore[ARC004] -- factory cycle
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
        return Installer(
            lambda app, container: install(app, self, container),
            before_routes=True,
            before_features=True,
        )

    def _resource_providers(
        self, providers: tuple[Provider, ...]
    ) -> tuple[Provider, ...]:
        if not (self.tracing or self.metrics) or self.resources == ():
            return ()
        from fastapi_canon.integrations.opentelemetry.resources import (
            default_resources,
            resource_providers,
        )

        adapters = default_resources() if self.resources is None else self.resources
        return resource_providers(providers, adapters)


Instrumentation.integration = Telemetry


class PortRegistration(NamedTuple):
    """One instrumented port in composition order."""

    feature: str
    port: type[Any]
    spec: Instrumentation


def instrumentation_providers(
    registrations: tuple[tuple[str, Instrumentation], ...],
    options: Telemetry,
    errors: ErrorLookup | None = None,
) -> tuple[Provider, ...]:
    ports = tuple(
        PortRegistration(feature, port, spec)
        for feature, spec in registrations
        for port in spec.ports
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

    return create_instrumentation_providers(ports, options, errors)


def instrumented_methods(
    port: type[Any],
) -> tuple[tuple[str, Callable[..., Any]], ...]:
    return tuple(
        (name, method)
        for name, method in inspect.getmembers(port, inspect.iscoroutinefunction)
        if name == "__call__" or not name.startswith("_")
    )


def _reject_borrowed_with_sdk(options: Telemetry) -> None:
    borrowed = (
        options.tracer_provider,
        options.meter_provider,
        options.logger_provider,
        options.shutdown,
    )
    if options.sdk is not None and any(item is not None for item in borrowed):
        raise FeatureConfigurationError(
            "sdk owns the providers; remove tracer_provider, meter_provider, "
            "logger_provider and shutdown"
        )


def _sdk_resource(sdk: SdkFactory) -> Callable[[], Iterator[SdkProviders]]:
    def enter() -> Iterator[SdkProviders]:
        with sdk() as providers:
            if not isinstance(providers, SdkProviders):
                raise FeatureConfigurationError("Telemetry sdk must yield SdkProviders")
            yield providers

    enter.__annotations__ = {"return": Iterator[SdkProviders]}
    return enter


def _owned(options: Telemetry) -> Callable[[SdkProviders], Telemetry]:
    from typing import Annotated

    from dishka import FromComponent

    def owned(providers: SdkProviders) -> Telemetry:
        return replace(
            options,
            sdk=None,
            tracer_provider=providers.tracer_provider,
            meter_provider=providers.meter_provider,
            logger_provider=providers.logger_provider,
        )

    owned.__annotations__ = {
        "providers": Annotated[SdkProviders, FromComponent("")],
        "return": Telemetry,
    }
    return owned


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
