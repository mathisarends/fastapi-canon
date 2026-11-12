from collections.abc import AsyncIterator

from fastapi import FastAPI

from fastapi_canon import APIRouter, Composition, Feature, export_contracts
from fastapi_canon.contracts import ContractKind
from fastapi_canon.integrations.webrtc import Session
from fastapi_canon.integrations.websockets import RpcChannel


def test_one_router_exports_http_sse_rpc_and_webrtc_with_effective_paths() -> None:
    called: list[str] = []
    router = APIRouter(prefix="/projects")

    @router.get("/{project_id}", operation_id="get_project")
    async def project(project_id: str) -> str:
        called.append("http")
        return project_id

    @router.sse("/{project_id}/events", operation_id="project_events")
    async def events(project_id: str) -> AsyncIterator[str]:
        called.append("sse")
        yield project_id

    projects = RpcChannel()
    tasks = RpcChannel()

    @projects.method("rename")
    async def rename() -> None:
        called.append("rpc")

    @tasks.method("create")
    async def create() -> None:
        called.append("rpc")

    session = RpcChannel()
    session.include_channel(projects, namespace="projects")
    session.include_channel(tasks, namespace="tasks")
    router.include_channel(session, path="/{project_id}/session")

    @router.webrtc("/{project_id}/media", name="project_media")
    async def media(project_id: str) -> Session:
        called.append(project_id)
        return Session()

    parent = APIRouter(prefix="/v1")
    parent.include_router(router)
    app = Composition(features=[Feature(name="projects", routers=[parent])]).build(
        FastAPI()
    )
    contracts = export_contracts(app)
    assert called == []
    assert contracts.openapi is not None
    assert set(contracts.openapi["paths"]) == {
        "/v1/projects/{project_id}",
        "/v1/projects/{project_id}/events",
    }
    stream = contracts.openapi["paths"]["/v1/projects/{project_id}/events"]["get"]
    assert stream["x-canon-stream"]["completeEvent"] == "canon.complete"
    assert contracts.openrpc is not None
    assert {method["name"] for method in contracts.openrpc["methods"]} == {
        "projects.rename",
        "tasks.create",
    }
    assert contracts.openrpc["servers"][0]["url"] == (
        "{baseUrl}/v1/projects/{project_id}/session"
    )
    assert contracts.webrtc is not None
    assert contracts.webrtc["subprotocol"] == "fastapi-webrtc.v1"
    assert contracts.webrtc["sessions"][0]["path"] == "/v1/projects/{project_id}/media"
    assert contracts.webrtc["sessions"][0]["name"] == "project_media"


def test_multiple_channel_endpoints_with_the_same_suffix_export_separately() -> None:
    projects = RpcChannel()
    tasks = RpcChannel()

    @projects.method("rename")
    async def rename() -> None:
        return None

    @tasks.method("create")
    async def create() -> None:
        return None

    router = APIRouter(prefix="/projects")
    router.include_channel(projects, path="/{project_id}/session", namespace="projects")
    router.include_channel(tasks, path="/{project_id}/tasks/session", namespace="tasks")
    app = FastAPI()
    app.include_router(router)
    contract = export_contracts(app, kinds=[ContractKind.OPENRPC]).openrpc
    assert contract is not None
    assert {server["url"] for server in contract["servers"]} == {
        "{baseUrl}/projects/{project_id}/session",
        "{baseUrl}/projects/{project_id}/tasks/session",
    }
    assert {server["name"] for server in contract["servers"]} == {
        "rpc_projects_project_id_session",
        "rpc_projects_project_id_tasks_session",
    }
    assert {method["name"] for method in contract["methods"]} == {
        "projects.rename",
        "tasks.create",
    }
