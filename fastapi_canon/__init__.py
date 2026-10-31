"""Opinionated FastAPI feature composition with Dishka and Problem Details."""

from .error import Error, ErrorConfigurationError, ErrorRegistry, Problem
from .exceptions import ConfigurationError
from .feature import (
    Composition,
    ErrorOptions,
    ExceptionHandlerSpec,
    Feature,
    FeatureConfigurationError,
    ProviderFactory,
    ProviderSource,
    RouterFactory,
)
from .openapi import install_openapi_contracts, validate_openapi_contracts
from .response import CanonResponse, ResponseConfigurationError, ResponseHeaders
from .router import CanonRouter

__all__ = [
    "CanonResponse",
    "CanonRouter",
    "Composition",
    "ConfigurationError",
    "Error",
    "ErrorConfigurationError",
    "ErrorOptions",
    "ErrorRegistry",
    "ExceptionHandlerSpec",
    "Feature",
    "FeatureConfigurationError",
    "Problem",
    "ProviderFactory",
    "ProviderSource",
    "ResponseConfigurationError",
    "ResponseHeaders",
    "RouterFactory",
    "install_openapi_contracts",
    "validate_openapi_contracts",
]
