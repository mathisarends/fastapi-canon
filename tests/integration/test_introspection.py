from dataclasses import FrozenInstanceError, asdict

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from fastapi_canon import (
    CanonRouter,
    Composition,
    Error,
    ErrorOptions,
    ErrorRegistry,
    Feature,
    FeatureConfigurationError,
    RouterPolicy,
)


class NotAuthenticated(Exception):
    pass


class ProjectMissing(Exception):
    pass


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
    auth = ErrorRegistry(errors=[auth_error])
    projects = ErrorRegistry(errors=[project_error])
    router = CanonRouter(prefix="/projects", error_registry=projects)
    router.get(
        "/{project_id}",
        operation_id="projects.get",
        raises=[project_error],
        dependencies=[Depends(require_project)],
    )(lambda project_id: {"id": project_id})
    composition = Composition(
        features=(Feature(name="projects", routers=[router]),),
        errors=ErrorOptions(type_base="https://example.test/problems"),
        router_factory=lambda: CanonRouter(prefix="/api/v1"),
        router_policy=RouterPolicy(
            error_registry=auth,
            raises=[auth_error],
            dependencies=[Depends(require_authenticated_user)],
        ),
    )
    app = composition.apply(FastAPI())

    snapshot = composition.inspect(app)
    operation = snapshot.operation("projects.get")

    assert operation.feature_name == "projects"
    assert operation.path == "/api/v1/projects/{project_id}"
    assert operation.methods == ("GET",)
    assert [
        (dep.name.rsplit(".", 1)[-1], dep.origin) for dep in operation.dependencies
    ] == [
        ("require_authenticated_user", "composition.router_policy"),
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
            "composition.router_policy",
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
    assert snapshot.operation("projects.get").path == "/api/v1/projects/{project_id}"
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
    app = composition.apply(FastAPI())

    info = composition.inspect(app)
    operation = info.operation("projects.list")
    hidden = info.operation("projects.internal")

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
    composition.apply(app)
    snapshot = composition.inspect(app)
    router.get("/later", operation_id="later")(lambda: "later")
    assert composition.inspect(app) == snapshot
    assert tuple(operation.operation_id for operation in snapshot.operations) == (
        "first",
    )
    with pytest.raises(LookupError, match="found 0"):
        snapshot.operation("later")
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
    composition.apply(app)
    operation = composition.inspect(app).operation("projects.list")
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
