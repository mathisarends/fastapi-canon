import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from fastapi_canon import Composition, Feature
from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integrations.mcp import McpOptions


@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/{rest:path}"])
@pytest.mark.parametrize("on_app", [False, True], ids=["feature", "application"])
def test_http_routes_cannot_shadow_mcp(path: str, on_app: bool) -> None:
    app = FastAPI()
    router = APIRouter()

    @router.post(path)
    async def conflict() -> str:
        return "wrong endpoint"

    if on_app:
        app.include_router(router)
    composition = Composition(
        features=[] if on_app else [Feature(name="conflict", routers=[router])],
        mcp=McpOptions(),
    )
    routes = tuple(app.routes)

    report = composition.check(app)

    assert report.has_errors
    assert any(issue.code == "canon.mcp.E001" for issue in report.issues)
    with pytest.raises(FeatureConfigurationError, match=r"MCP path.*conflicts"):
        composition.build(app)
    assert tuple(app.routes) == routes


def test_conflicts_include_router_factory_prefixes() -> None:
    router = APIRouter(prefix="/v1")

    @router.get("/mcp/")
    async def conflict() -> str:
        return "wrong endpoint"

    composition = Composition(
        features=[Feature(name="conflict", routers=[router])],
        router_factory=lambda: APIRouter(prefix="/api"),
        mcp=McpOptions(path="/api/v1/mcp"),
    )

    with pytest.raises(FeatureConfigurationError, match=r"MCP path.*conflicts"):
        composition.build(FastAPI())


def test_existing_parent_mount_cannot_shadow_mcp() -> None:
    app = FastAPI()
    app.mount("/", FastAPI())

    with pytest.raises(FeatureConfigurationError, match=r"MCP path.*conflicts"):
        Composition(mcp=McpOptions()).build(app)


def test_another_http_method_cannot_block_the_slashless_mcp_redirect() -> None:
    router = APIRouter()

    @router.put("/mcp")
    async def conflict() -> str:
        return "wrong endpoint"

    composition = Composition(
        features=[Feature(name="conflict", routers=[router])], mcp=McpOptions()
    )

    with pytest.raises(FeatureConfigurationError, match=r"MCP path.*conflicts"):
        composition.build(FastAPI())


def test_neighboring_http_route_remains_accessible() -> None:
    router = APIRouter()

    @router.get("/mcp-status")
    async def status() -> str:
        return "ready"

    composition = Composition(
        features=[Feature(name="status", routers=[router])], mcp=McpOptions()
    )
    app = FastAPI()
    assert not composition.check(app).has_errors
    composition.build(app)

    with TestClient(app) as client:
        assert client.get("/mcp-status").json() == "ready"
