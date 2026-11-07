# A complete HTTP feature

Runnable HTTP example; paths are relative to the application's `src/`.
Create empty `__init__.py` files for the other packages. The fixed-record demo
repository stands in for a persistent adapter. Requires `fastapi-canon` and
`uvicorn` in the consumer environment.

## Domain and application

### `backend/features/projects/domain/projects.py`

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    title: str


class ProjectMissing(Exception):
    pass
```

### `backend/features/projects/domain/__init__.py`

```python
from .projects import Project, ProjectMissing

__all__ = ["Project", "ProjectMissing"]
```

### `backend/features/projects/application/projects.py`

```python
from abc import ABC, abstractmethod

from backend.features.projects.domain import Project, ProjectMissing


class ProjectRepository(ABC):
    @abstractmethod
    async def get(self, project_id: int) -> Project | None: ...


class ProjectService:
    def __init__(self, project_repository: ProjectRepository) -> None:
        self._project_repository = project_repository

    async def get(self, project_id: int) -> Project:
        project = await self._project_repository.get(project_id)
        if project is None:
            raise ProjectMissing()
        return project
```

### `backend/features/projects/application/__init__.py`

```python
from .projects import ProjectRepository, ProjectService

__all__ = ["ProjectRepository", "ProjectService"]
```

## Infrastructure and DI

### `backend/features/projects/infrastructure/repository.py`

```python
from backend.features.projects.application import ProjectRepository
from backend.features.projects.domain import Project


class DemoProjectRepository(ProjectRepository):
    async def get(self, project_id: int) -> Project | None:
        if project_id == 1:
            return Project(id=1, title="First project")
        return None
```

### `backend/features/projects/infrastructure/di.py`

```python
from dishka import Provider, Scope, provide

from backend.features.projects.application import ProjectRepository, ProjectService
from backend.features.projects.infrastructure.repository import DemoProjectRepository


class ProjectsProvider(Provider):
    scope = Scope.REQUEST
    project_repository = provide(DemoProjectRepository, provides=ProjectRepository)
    project_service = provide(ProjectService)
```

Keep port bindings in this static form (`provide(Impl, provides=Port)` or a
`@provide` factory annotated with the port that returns the implementation's
constructor). `fastapi-canon check errors` reads these bindings to follow calls
on `ProjectRepository` into `DemoProjectRepository` without importing the app.

## Presentation contracts

### `backend/features/projects/presentation/errors.py`

```python
from fastapi_canon import Error

from backend.features.projects.domain import ProjectMissing

PROJECT_MISSING = Error(
    ProjectMissing,
    status=404,
    code="project_missing",
    title="Project not found",
    detail="Choose another project.",
)
```

### `backend/features/projects/presentation/schemas.py`

```python
from pydantic import BaseModel


class ProjectResponse(BaseModel):
    id: int
    title: str
```

### `backend/features/projects/presentation/router.py`

```python
from fastapi_canon import APIRouter, Inject

from backend.features.projects.application import ProjectService

from .errors import PROJECT_MISSING
from .schemas import ProjectResponse

router = APIRouter(
    namespace="projects",
    prefix="/projects",
    tags=["projects"],
)


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    raises=[PROJECT_MISSING],
)
async def get(
    project_id: int,
    project_service: Inject[ProjectService],
) -> ProjectResponse:
    project = await project_service.get(project_id)
    return ProjectResponse(id=project.id, title=project.title)
```

`raises=` registers runtime exception mappings and declares the operation's
public failures. Keep arbitrary exception text out of public details. Decorate
failing dependencies such as authentication with `@raises(error)` from
`fastapi_canon`; every operation using them then declares those errors
automatically. For shared auth use `RouterPolicy` with real dependencies;
declarations do not authorize requests. For empty/binary success use `CanonResponse.empty()` /
`CanonResponse.binary(...)` and return the matching response. Details:
[problems.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/problems.md) and [composition.md](https://github.com/mathisarends/fastapi_canon/blob/main/docs/composition.md).

## Operation IDs

The router's `namespace="projects"` and handler name `get` produce the OpenAPI
ID `projects.get` and SDK member `client.projects.get(...)`. Use the same rule
for HTTP and typed SSE:

- An explicit `operation_id=` wins unchanged. Otherwise Canon uses
  `<namespace>.<function_name>`, or just `<function_name>` without a namespace.
  A custom FastAPI `generate_unique_id_function=` replaces this default rule.
- Choose a stable namespace once per router. URL prefixes, parameter/return
  types, route `name=` and `Feature.name` do not determine operation IDs.
  Included Canon routers retain their own naming policy.
- Renaming a handler changes its automatic ID. To rename `get` to `get_project`
  while preserving the SDK contract, set `operation_id="projects.get"`.
  Preserve existing published IDs when adopting namespaces, then regenerate
  clients if the public names intentionally change.
- IDs must be unique across schema-visible HTTP/SSE operations. Collisions are
  configuration errors; never resolve them by automatically adding numbers,
  URL fragments or method suffixes. Use distinct namespaces, function names or
  explicit IDs. Repeated mounts and multiple HTTP methods need distinct
  operation declarations when they represent separate public operations.

Verify the installed `APIRouter` signature before using `namespace=`. For an
older release without that parameter, retain explicit `operation_id` values.
Run the [composition and CLI checks](testing.md#composition-and-operation-ids)
before exporting contracts. See the
[router guide](https://github.com/mathisarends/fastapi_canon/blob/main/docs/routers.md#automatic-http-and-sse-operation-names)
for naming and compatibility details.

## Feature and application composition

### `backend/features/projects/feature.py`

```python
from fastapi_canon import Feature

from .infrastructure.di import ProjectsProvider
from .presentation.router import router

feature = Feature(name="projects", routers=[router], providers=[ProjectsProvider])
```

### `backend/composition.py`

```python
from fastapi_canon import Feature

from backend.features.projects.feature import feature as projects_feature

FEATURES: tuple[Feature, ...] = (projects_feature,)
```

### `backend/app.py`

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, ErrorOptions

from backend.composition import FEATURES

composition = Composition(
    features=FEATURES,
    errors=ErrorOptions(type_base="https://api.example.com/problems"),
    router_factory=lambda: APIRouter(prefix="/api/v1"),
)


def create_app() -> FastAPI:
    return composition.build(FastAPI(title="Projects API"))
```

### `backend/main.py`

```python
from backend.app import create_app

app = create_app()
```

Run `uv run uvicorn backend.main:app --reload` with the package installed.
`GET /api/v1/projects/1` returns the record; `/2` returns a 404 problem with code
`project_missing`. `Composition` installs the registry and Dishka lifecycle;
do not add separate `setup_dishka` or registry installation.
