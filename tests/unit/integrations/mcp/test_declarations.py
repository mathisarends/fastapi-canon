import pytest

from fastapi_canon import FeatureConfigurationError
from fastapi_canon.integrations.mcp import McpOptions


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "relative",
        "/mcp?x=1",
        "/mcp#fragment",
        "/mcp path",
        "/../mcp",
        "//mcp",
        "/mcp\\other",
    ],
)
def test_invalid_endpoint_path(path: str) -> None:
    with pytest.raises(FeatureConfigurationError, match="path"):
        McpOptions(path=path)


def test_endpoint_defaults() -> None:
    options = McpOptions()
    assert options.path == "/mcp"
    assert options.name is None


def test_endpoint_path_is_normalized() -> None:
    assert McpOptions(path="/agent/").path == "/agent"


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_server_name(name: str) -> None:
    with pytest.raises(FeatureConfigurationError, match="name"):
        McpOptions(name=name)


@pytest.mark.parametrize("parameter", ["stateless_http", "mask_error_details"])
def test_runtime_flags_reject_non_booleans(parameter: str) -> None:
    with pytest.raises(FeatureConfigurationError, match=parameter):
        McpOptions(**{parameter: "true"})  # type: ignore[arg-type]


def test_runtime_defaults_preserve_fastmcp_settings() -> None:
    options = McpOptions()
    assert options.stateless_http is None
    assert options.mask_error_details is None
    assert options.middleware == ()
