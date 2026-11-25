# An HTTP feature

A complete feature below `src/`. The demo repository stands in for SQL; add
empty `__init__.py` files where needed.

## Domain and application

```python
# backend/features/projects/domain/projects.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    title: str


class ProjectMissing(Exception):
    pass
```

```python
# backend/features/projects/application/projects.py
from abc import ABC, abstractmethod

from backend.features.projects.domain.projects import Project, ProjectMissing


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

## Infrastructure and wiring

```python
# backend/features/projects/infrastructure/repository.py
from backend.features.projects.application.projects import ProjectRepository
from backend.features.projects.domain.projects import Project


class DemoProjectRepository(ProjectRepository):
    async def get(self, project_id: int) -> Project | None:
        return Project(id=1, title="First project") if project_id == 1 else None
```

```python
# backend/features/projects/infrastructure/di.py
from dishka import Provider, Scope, provide

from backend.features.projects.application.projects import (
    ProjectRepository,
    ProjectService,
)
from backend.features.projects.infrastructure.repository import DemoProjectRepository


class ProjectsProvider(Provider):
    scope = Scope.REQUEST
    project_repository = provide(DemoProjectRepository, provides=ProjectRepository)
    project_service = provide(ProjectService)
```

Keep bindings static (`provide(Impl, provides=Port)` or an annotated `@provide`
factory): `fastapi-canon check errors` follows them without importing the app.

## Presentation

```python
# backend/features/projects/presentation/errors.py
from fastapi_canon import Error

from backend.features.projects.domain.projects import ProjectMissing

PROJECT_MISSING = Error(
    ProjectMissing,
    status=404,
    code="project_missing",
    title="Project not found",
    detail="Choose another project.",
)
```

```python
# backend/features/projects/presentation/router.py
from pydantic import BaseModel

from fastapi_canon import APIRouter, Inject

from backend.features.projects.application.projects import ProjectService

from .errors import PROJECT_MISSING


class ProjectResponse(BaseModel):
    id: int
    title: str


router = APIRouter(namespace="projects", prefix="/projects", tags=["projects"])


@router.get("/{project_id}", raises=[PROJECT_MISSING])
async def get(project_id: int, projects: Inject[ProjectService]) -> ProjectResponse:
    project = await projects.get(project_id)
    return ProjectResponse(id=project.id, title=project.title)
```

- `raises=` maps the exception at runtime and documents the failure in OpenAPI.
  Never put arbitrary exception text in public details.
- Shared auth goes into a `RouterPolicy` with real dependencies.
- Empty or binary success: `CanonResponse.empty()` / `CanonResponse.binary(...)`.

## Feature and app

```python
# backend/features/projects/feature.py
from fastapi_canon import Feature

from .infrastructure.di import ProjectsProvider
from .presentation.router import router

feature = Feature(name="projects", routers=[router], providers=[ProjectsProvider])
```

```python
# backend/composition.py
from backend.features.projects.feature import feature as projects

FEATURES = (projects,)
```

Build the app as in [composition.md](composition.md). `GET /api/v1/projects/1`
returns the record; `/2` returns a 404 `application/problem+json` with code
`project_missing`.

## Operation IDs

`namespace="projects"` plus the handler `get` gives `projects.get`; without a
namespace the function name alone is used. Prefixes, `name=` and `Feature.name`
do not affect it. An explicit `operation_id=` wins.

- Renaming a handler renames the SDK method. Keep it with
  `operation_id="projects.get"`.
- Duplicate IDs fail `build()` and `check composition` (`canon.routing.E002`).
  Fix them with a distinct namespace, name or explicit ID — never numeric
  suffixes.

Details: [routers](https://github.com/mathisarends/fastapi-canon/blob/main/docs/20-routers.md),
[errors](https://github.com/mathisarends/fastapi-canon/blob/main/docs/02-errors.md).
