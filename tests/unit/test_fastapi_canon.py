from importlib.metadata import metadata

import pytest

import fastapi_canon
import fastapi_canon.error

APPLICATION_NAMES = {
    "APIRouter",
    "CanonResponse",
    "Composition",
    "Error",
    "ErrorOptions",
    "Feature",
    "Inject",
    "ResponseHeaders",
    "RouterPolicy",
    "Scoped",
    "ServiceScope",
    "__version__",
    "export_contracts",
    "inspect_app",
}


def test_package_is_importable() -> None:
    assert fastapi_canon.error.__name__ == "fastapi_canon.error"


def test_package_declares_mit_license() -> None:
    package = metadata("fastapi-canon")

    assert package["License-Expression"] == "MIT"
    assert "License :: OSI Approved :: MIT License" in package.get_all("Classifier", [])


def test_root_exports_only_application_building_names() -> None:
    assert set(fastapi_canon.__all__) == APPLICATION_NAMES
    assert all(hasattr(fastapi_canon, name) for name in APPLICATION_NAMES)


@pytest.mark.parametrize(
    "name",
    [
        "RpcChannel",
        "Database",
        "ErrorRegistry",
        "CanonRouter",
        "HTTPMethod",
        "CheckReport",
    ],
)
def test_root_does_not_resolve_moved_names(name: str) -> None:
    assert not hasattr(fastapi_canon, name)
