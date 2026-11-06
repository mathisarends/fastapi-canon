from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from fastapi_canon import (
    APIRouter,
    Composition,
    Error,
    ErrorOptions,
    Feature,
)
from fastapi_canon.integrations.websockets import RpcChannel


class ProjectMissing(Exception):
    pass


class MissingFields(BaseModel):
    replacement: str | None = None


class Project(BaseModel):
    id: int


class ProjectEvent(BaseModel):
    sequence: int


project_missing = Error(
    ProjectMissing,
    status=404,
    code="project_missing",
    title="Project not found",
    detail="Choose another project.",
    extensions_model=MissingFields,
    extensions={"replacement": None},
)
router = APIRouter()
projects = RpcChannel("projects")


def require_project(project_id: int) -> None:
    if project_id == 0:
        raise ProjectMissing("private database information")


@router.get(
    "/projects/{project_id}",
    operation_id="get",
    tags=["projects"],
    raises=[project_missing],
)
async def get_project(project_id: int) -> Project:
    require_project(project_id)
    return Project(id=project_id)


@router.sse(
    "/projects/{project_id}/events",
    operation_id="events",
    tags=["projects"],
    raises=[project_missing],
    dependencies=[Depends(require_project)],
)
async def events(project_id: int) -> AsyncIterator[ProjectEvent]:
    yield ProjectEvent(sequence=1)
    if project_id == 2:
        raise ProjectMissing("private database information")


class CancelProject(BaseModel):
    project_id: int


@projects.method(raises=[project_missing])
async def cancel(params: CancelProject) -> None:
    require_project(params.project_id)


router.include_channel(projects, path="/ws")
feature = Feature(name="projects", routers=[router])


def create_app() -> FastAPI:
    return Composition(
        features=(feature,),
        errors=ErrorOptions(type_base="https://api.example.com/problems"),
    ).build(FastAPI(title="Projects"))


app = create_app()
