from copy import deepcopy
from typing import Any

from fastapi import FastAPI

from fastapi_canon.error import ErrorConfigurationError
from fastapi_canon.error.openapi import install_openapi


def export_openapi(app: FastAPI) -> dict[str, Any]:
    """Snapshot native HTTP/SSE contracts as OpenAPI 3.2 without mutating the cache."""
    _validate_app(app)
    document = deepcopy(app.openapi())
    version = document.get("openapi", "")
    if not version.startswith(("3.1.", "3.2.")):
        raise ValueError(f"Expected OpenAPI 3.1 or 3.2, got {version!r}")
    if (
        document.get("jsonSchemaDialect")
        == "https://spec.openapis.org/oas/3.1/dialect/base"
    ):
        document["jsonSchemaDialect"] = (
            "https://spec.openapis.org/oas/3.2/dialect/2026-02-26"
        )
    document["openapi"] = "3.2.0"
    return document


def install_openapi_contracts(app: FastAPI) -> None:
    """Install success-contract OpenAPI compilation on a FastAPI application."""
    _validate_app(app)
    if app.openapi_schema is not None:
        msg = "install OpenAPI contracts before generating or caching OpenAPI"
        raise ErrorConfigurationError(msg)
    install_openapi(
        None,
        app,
        include_validation_error=False,
        include_http_exceptions=False,
    )


def validate_openapi_contracts(app: FastAPI) -> None:
    """Generate OpenAPI now so all installed canon contracts are validated."""
    _validate_app(app)
    app.openapi()


def _validate_app(app: object) -> None:
    raw_app: object = app
    if not isinstance(raw_app, FastAPI):
        msg = "app must be a FastAPI instance"
        raise ErrorConfigurationError(msg)
