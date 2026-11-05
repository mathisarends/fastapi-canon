"""Opinionated FastAPI feature composition with Dishka and Problem Details."""

from http import HTTPMethod
from typing import TYPE_CHECKING

from ._version import __version__
from .api_router import APIRouter
from .contracts import Contracts, export_contracts
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
from .policy import RouterPolicy
from .public_exports import __getattr__ as __getattr__
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
    from .integrations.websockets import RpcChannel

__all__ = [
    "APIRouter",
    "AlembicMigrations",
    "CanonResponse",
    "CanonRouter",
    "Composition",
    "ConfigurationError",
    "Contracts",
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
    "RpcChannel",
    "ServiceScope",
    "TransactionLocks",
    "__version__",
    "export_contracts",
    "install_openapi_contracts",
    "run_alembic_environment",
    "validate_openapi_contracts",
]
