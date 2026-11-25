# A feature

```python
# presentation/errors.py
PROJECT_MISSING = Error(
    ProjectMissing, status=404, code="project_missing", title="Project not found"
)

# presentation/router.py
router = APIRouter(namespace="projects", prefix="/projects")


@router.get("/{project_id}", raises=[PROJECT_MISSING])
async def get(project_id: int, projects: Inject[ProjectService]) -> ProjectResponse:
    project = await projects.get(project_id)
    return ProjectResponse(id=project.id, title=project.title)


# infrastructure/di.py
class ProjectsProvider(Provider):
    scope = Scope.REQUEST
    repository = provide(SqlProjectRepository, provides=ProjectRepository)
    service = provide(ProjectService)


# feature.py
feature = Feature(name="projects", routers=[router], providers=[ProjectsProvider])

# app.py
composition = Composition(
    features=(feature,),
    errors=ErrorOptions(type_base="https://api.example.com/problems"),
    router_factory=lambda: APIRouter(prefix="/api/v1"),
)


def create_app() -> FastAPI:
    return composition.build(FastAPI())
```

- `ProjectMissing` is a plain domain exception; `raises=` turns it into a 404
  Problem Details response and documents it in OpenAPI.
- The operation ID is `<namespace>.<function>` (`projects.get`).
- Keep `provide(Impl, provides=Port)` static so `check errors` can follow it.
- Also on `Feature`: `errors`, `lifespan`, `checks`, `integrations`.
  On `Composition`: `integrations`, `overrides`, `request_logging`, `middleware`.
- Shared auth: `APIRouter(policy=RouterPolicy(...))`. Empty/binary responses:
  `CanonResponse`. Tracing: `fastapi_canon.integrations.opentelemetry`.

Guides: [composition](https://github.com/mathisarends/fastapi-canon/blob/main/docs/05-composition.md),
[errors](https://github.com/mathisarends/fastapi-canon/blob/main/docs/02-errors.md).
