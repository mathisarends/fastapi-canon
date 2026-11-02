"""Opinionated FastAPI feature composition with Dishka and Problem Details."""

from http import HTTPMethod
from typing import TYPE_CHECKING

from ._version import __version__
from .error import Error, ErrorConfigurationError, ErrorRegistry, Problem
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
from .integrations.database import __getattr__ as __getattr__
from .openapi import install_openapi_contracts, validate_openapi_contracts
from .policy import RouterPolicy
from .response import CanonResponse, ResponseConfigurationError, ResponseHeaders
from .router import CanonRouter
from .scopes import ServiceScope

if TYPE_CHECKING:
    from .integrations.database import (
        AlembicMigrations,
        Database,
        TransactionLocks,
        run_alembic_environment,
    )

__all__ = [
    "AlembicMigrations",
    "CanonResponse",
    "CanonRouter",
    "Composition",
    "Database",
    "Error",
    "ErrorConfigurationError",
    "ErrorOptions",
    "ErrorRegistry",
    "ExceptionHandlerSpec",
    "Feature",
    "FeatureConfigurationError",
    "HTTPMethod",
    "Problem",
    "ProviderFactory",
    "ProviderSource",
    "ResponseConfigurationError",
    "ResponseHeaders",
    "RouterFactory",
    "RouterPolicy",
    "ServiceScope",
    "TransactionLocks",
    "__version__",
    "install_openapi_contracts",
    "run_alembic_environment",
    "validate_openapi_contracts",
]
