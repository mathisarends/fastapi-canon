import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import FrozenInstanceError, asdict
from functools import wraps

import pytest
from dishka import FromDishka, Provider, Scope, provide
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from fastapi_canon import (
    APIRouter as ApplicationRouter,
)
from fastapi_canon import (
    Composition,
    Error,
    ErrorOptions,
    Feature,
    Inject,
    RouterPolicy,
)
from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integrations.sse.canon import CanonStreamRouter
from fastapi_canon.router import CanonRouter


class NotAuthenticated(Exception):
    pass


class ProjectMissing(Exception):
    pass


class ProjectService:
    name = "project"


def require_authenticated_user() -> None:
    return None


def require_project() -> None:
    return None


def test_snapshot_records_effective_paths_dependencies_and_error_origins() -> None:
    auth_error = Error(
        NotAuthenticated,
        code="authentication_required",
        status=401,
        title="Authentication required",
    )
    project_error = Error(
        ProjectMissing, code="project_missing", status=404, title="Project missing"
    )
    router = CanonRouter(prefix="/projects")
    router.get(
        "/{project_id}",
        operation_id="projects.get",
        raises=[project_error],
        dependencies=[Depends(require_project)],
    )(lambda project_id: {"id": project_id})
    composition = Composition(
        features=(Feature(name="projects", routers=[router]),),
        errors=ErrorOptions(type_base="https://example.test/problems"),
        router_factory=lambda: CanonRouter(
            prefix="/api/v1",
            policy=RouterPolicy(
                raises=[auth_error],
                dependencies=[Depends(require_authenticated_user)],
            ),
        ),
    )
    app = composition.build(FastAPI())

    snapshot = composition.inspect(app)
    operation = snapshot.operation_by_id("projects.get")

    assert operation.feature_name == "projects"
    assert operation.path == "/api/v1/projects/{project_id}"
    assert operation.methods == ("GET",)
    assert [
        (dep.name.rsplit(".", 1)[-1], dep.origin) for dep in operation.dependencies
    ] == [
        ("require_authenticated_user", "composition.router_factory"),
        ("require_project", "operation"),
    ]
    assert [
        (error.code, error.type_uri, error.status, error.origin)
        for error in operation.errors
    ] == [
        (
            "authentication_required",
            "https://example.test/problems/authentication_required",
            401,
            "composition.router_factory",
        ),
        (
            "project_missing",
            "https://example.test/problems/project_missing",
            404,
            "operation",
        ),
    ]
    assert snapshot.features[0].operations == (operation,)
    with pytest.raises(FrozenInstanceError):
        operation.path = "/changed"
    copied = asdict(snapshot)
    copied["operations"][0]["path"] = "/changed"
    assert (
        snapshot.operation_by_id("projects.get").path == "/api/v1/projects/{project_id}"
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/projects/1").json() == {"id": "1"}
    document = app.openapi()
    assert (
        document["paths"][operation.path]["get"]["operationId"]
        == operation.operation_id
    )
    assert "x-canon-provenance" not in str(document)


def test_nested_router_prefixes_and_hidden_routes_are_described() -> None:
    child = CanonRouter(
        prefix="/projects", policy=RouterPolicy(dependencies=[Depends(require_project)])
    )
    child.get("", operation_id="projects.list")(lambda: [])
    child.get("/internal", operation_id="projects.internal", include_in_schema=False)(
        lambda: "ok"
    )
    parent = APIRouter(prefix="/workspace")
    parent.include_router(child, prefix="/v2")
    composition = Composition(
        features=(Feature(name="projects", routers=[parent]),),
        router_factory=lambda: CanonRouter(prefix="/api"),
    )
    app = composition.build(FastAPI())

    info = composition.inspect(app)
    operation = info.operation_by_id("projects.list")
    hidden = info.operation_by_id("projects.internal")

    assert operation.path == "/api/workspace/v2/projects"
    assert operation.dependencies[0].origin == "router.policy"
    assert hidden.path == "/api/workspace/v2/projects/internal"
    assert not hidden.include_in_schema
    assert hidden.path not in app.openapi()["paths"]
    assert info.features[0].name == "projects"


def test_inspect_requires_the_matching_installation_and_keeps_a_snapshot() -> None:
    router = CanonRouter()
    router.get("/first", operation_id="first")(lambda: "first")
    composition = Composition(features=(Feature(name="projects", routers=[router]),))
    app = FastAPI()
    with pytest.raises(FeatureConfigurationError, match="must be applied"):
        composition.inspect(app)
    composition.build(app)
    snapshot = composition.inspect(app)
    router.get("/later", operation_id="later")(lambda: "later")
    assert composition.inspect(app) == snapshot
    assert tuple(operation.operation_id for operation in snapshot.operations) == (
        "first",
    )
    with pytest.raises(LookupError, match="found 0"):
        snapshot.operation_by_id("later")
    with pytest.raises(FeatureConfigurationError, match="must be applied"):
        Composition().inspect(app)


def test_application_and_factory_dependencies_keep_their_origins() -> None:
    router = CanonRouter()
    router.get("/projects", operation_id="projects.list")(lambda: [])
    app = FastAPI(dependencies=[Depends(require_authenticated_user)])
    composition = Composition(
        features=(Feature(name="projects", routers=[router]),),
        router_factory=lambda: CanonRouter(
            prefix="/api", dependencies=[Depends(require_project)]
        ),
    )
    composition.build(app)
    operation = composition.inspect(app).operation_by_id("projects.list")
    assert operation.path == "/api/projects"
    assert [
        (dep.name.rsplit(".", 1)[-1], dep.origin) for dep in operation.dependencies
    ] == [
        ("require_authenticated_user", "application"),
        ("require_project", "composition.router_factory"),
    ]
    assert (
        app.openapi()["paths"][operation.path]["get"]["operationId"]
        == operation.operation_id
    )


def test_path_converters_share_openapi_paths_and_inherited_contracts() -> None:
    failure = Error(
        NotAuthenticated,
        code="authentication_required",
        status=401,
        title="Authentication required",
    )
    router = CanonRouter()
    router.get("/files/{path:path}", operation_id="files.get")(lambda path: path)
    composition = Composition(
        features=(Feature(name="files", routers=[router]),),
        errors=ErrorOptions(type_base="https://example.test/problems"),
        router_factory=lambda: CanonRouter(raises=[failure]),
    )
    app = composition.build(FastAPI())
    operation = composition.inspect(app).operation_by_id("files.get")
    assert operation.path == "/files/{path}"
    assert operation.errors[0].code == "authentication_required"
    assert "401" in app.openapi()["paths"][operation.path]["get"]["responses"]


def test_mounted_applications_keep_their_own_ownership_boundary() -> None:
    external = FastAPI()
    external.get("/external", operation_id="external.get")(lambda: "external")
    router = CanonRouter()
    router.get("/projects", operation_id="projects.list")(lambda: [])
    router.mount("/mounted", external)
    composition = Composition(features=(Feature(name="projects", routers=[router]),))
    app = composition.build(FastAPI())
    description = composition.inspect(app)
    assert tuple(op.operation_id for op in description.operations) == ("projects.list",)
    assert composition.check(app).complete


def test_endpoint_lookup_uses_the_registered_symbol_and_keeps_exports_data_only() -> (
    None
):
    calls: list[str] = []

    def get_project() -> str:
        calls.append("called")
        return "project"

    @wraps(get_project)
    def wrapped_project() -> str:
        return get_project()

    child = CanonRouter(prefix="/projects")
    child.get("/current", operation_id="projects.get")(wrapped_project)
    parent = APIRouter(prefix="/v1")
    parent.include_router(child)
    composition = Composition(features=(Feature(name="projects", routers=[parent]),))
    app = composition.build(FastAPI())
    description = composition.inspect(app)

    operation = description.operation(wrapped_project)
    assert operation.path == "/v1/projects/current"
    assert operation is description.operation_by_id("projects.get")
    assert calls == []
    with pytest.raises(LookupError, match="found 0"):
        description.operation(get_project)
    exported = asdict(description)
    assert set(exported) == {"features", "operations"}
    assert (
        json.loads(json.dumps(exported))["operations"][0]["operation_id"]
        == "projects.get"
    )


def test_endpoint_lookup_reports_every_ambiguous_registration() -> None:
    def get_project() -> str:
        return "project"

    router = CanonRouter()
    router.get("/projects", operation_id="projects.get")(get_project)
    router.get("/archive", operation_id="archive.get")(get_project)
    composition = Composition(features=(Feature(name="projects", routers=[router]),))
    description = composition.inspect(composition.build(FastAPI()))

    with pytest.raises(LookupError, match="found 2: GET /projects, GET /archive"):
        description.operation(get_project)
    assert description.operation_by_id("archive.get").path == "/archive"


def test_endpoint_lookup_keeps_the_declared_symbol_with_dishka_routes() -> None:
    router = CanonRouter(route_class=DishkaRoute)
    provider = Provider()
    provider.provide(ProjectService, scope=Scope.REQUEST)

    @router.get("/projects", operation_id="projects.get")
    async def get_project(service: FromDishka[ProjectService]) -> str:
        return service.name

    composition = Composition(
        features=(Feature(name="projects", routers=[router], providers=[provider]),)
    )
    app = composition.build(FastAPI())
    operation = composition.inspect(app).operation(get_project)
    assert operation.operation_id == "projects.get"
    assert operation.path == "/projects"
    with TestClient(app) as client:
        assert client.get(operation.path).json() == "project"


@pytest.mark.parametrize("injected", [False, True])
@pytest.mark.parametrize("synchronous", [False, True])
@pytest.mark.parametrize("operation_id", [None, "stable.events"])
def test_sse_lookup_keeps_declarations_across_inclusion_and_injection(
    injected: bool,
    synchronous: bool,
    operation_id: str | None,
) -> None:
    calls: list[str] = []

    class Services(Provider):
        @provide(scope=Scope.APP)
        def service(self) -> ProjectService:
            calls.append("provider")
            return ProjectService()

    child = ApplicationRouter(prefix="/projects", namespace="projects")
    match synchronous, injected:
        case False, False:

            @child.sse("/events", operation_id=operation_id)
            async def events() -> AsyncIterator[str]:
                calls.append("generator")
                yield "project"
        case False, True:

            @child.sse("/events", operation_id=operation_id)
            async def events(service: Inject[ProjectService]) -> AsyncIterator[str]:
                calls.append("generator")
                yield service.name
        case True, False:

            @child.sse("/events", operation_id=operation_id)
            def events() -> Iterator[str]:
                calls.append("generator")
                yield "project"
        case True, True:

            @child.sse("/events", operation_id=operation_id)
            def events(service: Inject[ProjectService]) -> Iterator[str]:
                calls.append("generator")
                yield service.name

    parent = ApplicationRouter(prefix="/v1")
    parent.include_router(child)
    composition = Composition(
        features=[Feature(name="streams", routers=[parent], providers=[Services()])],
        router_factory=lambda: ApplicationRouter(prefix="/api"),
    )
    app = composition.build(FastAPI())
    description = composition.inspect(app)
    operation = description.operation(events)

    assert operation.feature_name == "streams"
    assert operation.path == "/api/v1/projects/events"
    assert operation.methods == ("GET",)
    assert operation.operation_id == (operation_id or "projects.events")
    assert description.operation_by_id(operation.operation_id) is operation
    assert calls == []
    exported = json.loads(json.dumps(asdict(description)))
    assert set(exported) == {"features", "operations"}
    schema = json.loads(json.dumps(app.openapi()))
    assert (
        schema["paths"][operation.path]["get"]["operationId"] == operation.operation_id
    )

    @child.sse("/later")
    async def later() -> AsyncIterator[str]:
        yield "later"

    assert description.operations == (operation,)
    assert composition.inspect(app).operations == (operation,)
    with pytest.raises(LookupError, match="found 0"):
        description.operation(later)

    async def scenario() -> None:
        async with (
            asyncio.timeout(5),
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app), base_url="http://test", timeout=5
            ) as client,
        ):
            response = await client.get(operation.path)
            assert response.status_code == 200
            assert 'data: "project"' in response.text
            assert "canon.complete" in response.text

    asyncio.run(scenario())
    assert calls == (["provider", "generator"] if injected else ["generator"])


def test_sse_lookup_keeps_an_explicit_user_decorator() -> None:
    calls: list[str] = []

    async def source() -> AsyncIterator[str]:
        calls.append("generator")
        yield "project"

    @wraps(source)
    async def decorated() -> AsyncIterator[str]:
        async for item in source():
            yield item

    attributes = dict(vars(decorated))
    router = ApplicationRouter()
    router.sse("/events", operation_id="events")(decorated)
    composition = Composition(features=[Feature(name="streams", routers=[router])])
    description = composition.inspect(composition.build(FastAPI()))

    assert description.operation(decorated).path == "/events"
    with pytest.raises(LookupError, match="found 0"):
        description.operation(source)
    assert vars(decorated) == attributes
    assert calls == []


def test_sse_lookup_rejects_ambiguous_declarations() -> None:
    calls: list[str] = []

    async def events() -> AsyncIterator[str]:
        calls.append("generator")
        yield "project"

    router = ApplicationRouter()
    router.sse("/first", operation_id="first.events")(events)
    router.sse("/second", operation_id="second.events")(events)
    composition = Composition(features=[Feature(name="streams", routers=[router])])
    description = composition.inspect(composition.build(FastAPI()))

    with pytest.raises(LookupError, match="found 2: GET /first, GET /second"):
        description.operation(events)
    assert description.operation_by_id("first.events").path == "/first"
    assert description.operation_by_id("second.events").path == "/second"
    assert calls == []


def test_specialized_sse_lookup_preserves_explicit_dishka_declarations() -> None:
    calls: list[str] = []

    class Services(Provider):
        @provide(scope=Scope.APP)
        def service(self) -> ProjectService:
            calls.append("provider")
            return ProjectService()

    router = CanonStreamRouter(namespace="legacy", route_class=DishkaRoute)

    @router.stream("/events")
    async def events(service: FromDishka[ProjectService]) -> AsyncIterator[str]:
        calls.append("generator")
        yield service.name

    composition = Composition(
        features=[Feature(name="streams", routers=[router], providers=[Services()])]
    )
    description = composition.inspect(composition.build(FastAPI()))

    operation = description.operation(events)
    assert operation.operation_id == "legacy.events"
    assert operation.path == "/events"
    assert description.operation_by_id("legacy.events") is operation
    assert calls == []
