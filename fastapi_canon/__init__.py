"""Opinionated FastAPI feature composition with Dishka and Problem Details."""

from ._version import __version__
from .api_router import APIRouter
from .contracts import export_contracts
from .error import Error
from .feature import Composition, ErrorOptions, ExceptionHandlerSpec, Feature
from .injection import Inject
from .policy import RouterPolicy
from .response import CanonResponse, ResponseHeaders
from .scopes import Scoped, ServiceScope

__all__ = [
    "APIRouter",
    "CanonResponse",
    "Composition",
    "Error",
    "ErrorOptions",
    "ExceptionHandlerSpec",
    "Feature",
    "Inject",
    "ResponseHeaders",
    "RouterPolicy",
    "Scoped",
    "ServiceScope",
    "__version__",
    "export_contracts",
]
