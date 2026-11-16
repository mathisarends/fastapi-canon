from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dishka import (
    Provider,
)
from fastapi import APIRouter, FastAPI

from fastapi_canon.composition.checks import (
    check_installed,
    prepare_composition,
    raise_check_errors,
)
from fastapi_canon.composition.feature import (
    ErrorOptions,
    Feature,
    unique_instances,
    unique_provider_sources,
    validate_bool,
    validate_unique_feature_names,
)
from fastapi_canon.composition.installation import (
    INSTALLATION_STATE_KEY,
    Installation,
    apply_composition,
    installed_description,
    validate_applied_composition,
)
from fastapi_canon.diagnostics import (
    CheckIssue,
    CheckReport,
    CheckResult,
    CompositionInfo,
)
from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integration import Integration
from fastapi_canon.openapi import (
    validate_openapi_contracts,
)


@dataclass(frozen=True, slots=True, init=False)
class Composition:
    """An ordered, reusable declaration of an application's features."""

    features: tuple[Feature, ...]
    errors: ErrorOptions
    router_factory: Callable[[], APIRouter] | None
    integrations: tuple[Integration, ...]
    request_logging: bool
    overrides: tuple[Provider | Callable[[], Provider], ...]

    def __init__(
        self,
        *,
        features: Sequence[Feature] = (),
        errors: ErrorOptions | None = None,
        router_factory: Callable[[], APIRouter] | None = None,
        integrations: Sequence[Integration] = (),
        request_logging: bool = False,
    ) -> None:
        normalized_integrations = unique_instances(
            integrations, Integration, "integrations"
        )
        enabled: dict[type[Integration], int] = {}
        for index, integration in enumerate(normalized_integrations):
            previous = enabled.setdefault(type(integration), index)
            if previous != index:
                msg = (
                    f"integrations[{index}] enables {type(integration).__name__} "
                    f"again; integrations[{previous}] already configures it"
                )
                raise FeatureConfigurationError(msg)
        object.__setattr__(self, "integrations", normalized_integrations)
        validate_bool(request_logging, "request_logging")
        object.__setattr__(self, "request_logging", request_logging)
        normalized_features = unique_instances(features, Feature, "features")
        validate_unique_feature_names(normalized_features)
        raw_errors: object = errors
        raw_router_factory: object = router_factory
        if raw_errors is not None and not isinstance(raw_errors, ErrorOptions):
            msg = "errors must be an ErrorOptions instance or None"
            raise FeatureConfigurationError(msg)
        if raw_router_factory is not None and not callable(raw_router_factory):
            msg = "router_factory must be callable or None"
            raise FeatureConfigurationError(msg)
        object.__setattr__(self, "features", normalized_features)
        object.__setattr__(
            self, "errors", errors if errors is not None else ErrorOptions()
        )
        object.__setattr__(self, "router_factory", router_factory)
        object.__setattr__(self, "overrides", ())

    def build(self, app: FastAPI) -> FastAPI:
        """Install this composition into *app*, validate it and return the app.

        Building the same composition again only repeats validation.
        """
        if not apply_composition(app, self):
            raise_check_errors(self.check(app))
        # Regenerate OpenAPI so routes added since an earlier build are included.
        app.openapi_schema = None
        validate_openapi_contracts(app)
        return app

    def override(self, *providers: Provider | Callable[[], Provider]) -> Composition:
        """Return a copy whose *providers* replace existing Dishka bindings.

        Overrides are added after every feature provider, so their bindings win.
        Each override may only provide types that a feature already provides.
        """
        if not providers:
            raise FeatureConfigurationError("override expects at least one provider")
        overrides = unique_provider_sources(
            (*self.overrides, *providers), owner="Composition overrides"
        )
        copy = object.__new__(Composition)
        for name in Composition.__dataclass_fields__:
            object.__setattr__(copy, name, getattr(self, name))
        object.__setattr__(copy, "overrides", overrides)
        return copy

    def check(self, app: FastAPI) -> CheckReport:
        """Check configuration without starting lifespans or resolving services."""
        if not isinstance(app, FastAPI):
            raise FeatureConfigurationError("app must be a FastAPI instance")
        installed = getattr(app.state, INSTALLATION_STATE_KEY, None)
        if installed is not None:
            try:
                validate_applied_composition(app, self)
            except FeatureConfigurationError as error:
                return CheckReport(
                    (
                        CheckIssue(
                            "canon.composition.E001",
                            str(error),
                            "Use the composition installed on this application.",
                        ),
                    ),
                    (CheckResult("installation", "failed"),),
                )
            assert isinstance(installed, Installation)
            return check_installed(app, installed.description, self.features)
        return prepare_composition(app, self).report


def inspect_app(app: FastAPI) -> CompositionInfo:
    """Return the HTTP snapshot recorded when a Composition was built into *app*.

    The snapshot is detached: routes added after the build are not included.
    """
    raw_app: object = app
    if not isinstance(raw_app, FastAPI):
        raise FeatureConfigurationError("app must be a FastAPI instance")
    return installed_description(app)
