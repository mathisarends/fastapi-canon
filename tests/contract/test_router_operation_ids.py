from collections.abc import AsyncIterator

import pytest
from fastapi import APIRouter as FastAPIRouter
from fastapi import FastAPI
from fastapi.routing import APIRoute

from fastapi_canon import (
    APIRouter,
    Composition,
    Feature,
    FeatureConfigurationError,
)
from fastapi_canon.integrations.sse import CanonStreamRouter


@pytest.mark.parametrize("namespace", [None, "projects", "organization.projects"])
def test_http_and_sse_ids_follow_the_declaring_namespace(namespace: str | None) -> None:
    router = APIRouter(namespace=namespace, prefix="/unrelated")

    @router.get("/{project_id}", name="route_lookup")
    def get_project(project_id: int) -> int:
        return project_id

    @router.sse("/{project_id}/events", name="route_subscription")
    async def events(project_id: int) -> AsyncIterator[int]:
        yield project_id

    parent = APIRouter(namespace="parent", prefix="/v1")
    parent.include_router(router)
    app = Composition(features=[Feature(name="feature_name", routers=[parent])]).build(
        FastAPI()
    )
    prefix = f"{namespace}." if namespace is not None else ""
    paths = app.openapi()["paths"]
    assert (
        paths["/v1/unrelated/{project_id}"]["get"]["operationId"]
        == prefix + "get_project"
    )
    assert (
        paths["/v1/unrelated/{project_id}/events"]["get"]["operationId"]
        == prefix + "events"
    )


def test_explicit_operation_ids_override_automatic_http_and_sse_names() -> None:
    router = APIRouter(namespace="projects")

    @router.get("/current", operation_id="stable.read")
    def renamed_read() -> str:
        return "current"

    @router.sse("/updates", operation_id="stable.events")
    async def renamed_events() -> AsyncIterator[str]:
        yield "updated"

    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    assert paths["/current"]["get"]["operationId"] == "stable.read"
    assert paths["/updates"]["get"]["operationId"] == "stable.events"


def test_specialized_sse_router_uses_the_same_naming_rule() -> None:
    router = CanonStreamRouter(namespace="projects")

    @router.stream("/events")
    async def events() -> AsyncIterator[str]:
        yield "updated"

    app = FastAPI()
    app.include_router(router)
    assert app.openapi()["paths"]["/events"]["get"]["operationId"] == "projects.events"


def test_custom_id_generator_is_shared_with_the_sse_adapter() -> None:
    def generate_id(route: APIRoute) -> str:
        return f"custom.{route.name}"

    router = APIRouter(namespace="projects", generate_unique_id_function=generate_id)

    @router.get("/current", name="read")
    def current() -> str:
        return "current"

    @router.sse("/updates", name="watch")
    async def updates() -> AsyncIterator[str]:
        yield "updated"

    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    assert paths["/current"]["get"]["operationId"] == "custom.read"
    assert paths["/updates"]["get"]["operationId"] == "custom.watch"


def test_parent_names_included_native_fastapi_endpoints() -> None:
    child = FastAPIRouter()

    @child.get("/native")
    def native() -> str:
        return "native"

    parent = APIRouter(namespace="parent")
    parent.include_router(child)
    app = FastAPI()
    app.include_router(parent)
    assert app.openapi()["paths"]["/native"]["get"]["operationId"] == "parent.native"


def test_duplicate_ids_block_composition_before_application_mutation() -> None:
    router = APIRouter(namespace="projects")

    def events() -> str:
        return "updated"

    router.get("/current")(events)
    router.get("/archive")(events)
    composition = Composition(features=[Feature(name="projects", routers=[router])])
    app = FastAPI()
    routes = tuple(app.routes)

    with pytest.raises(
        FeatureConfigurationError, match=r"Duplicate operation ID 'projects\.events'"
    ):
        composition.build(app)
    assert tuple(app.routes) == routes
    assert app.openapi_schema is None


def test_distinct_namespaces_allow_reusing_an_endpoint() -> None:
    first = APIRouter(namespace="projects")
    second = APIRouter(namespace="archive")

    def events() -> str:
        return "updated"

    first.get("/current")(events)
    second.get("/archive")(events)
    app = Composition(
        features=[Feature(name="projects", routers=[first, second])]
    ).build(FastAPI())
    paths = app.openapi()["paths"]
    assert paths["/current"]["get"]["operationId"] == "projects.events"
    assert paths["/archive"]["get"]["operationId"] == "archive.events"


def test_repeated_router_mounts_do_not_add_suffixes_to_operation_ids() -> None:
    router = APIRouter(namespace="projects")

    @router.get("/events")
    def events() -> str:
        return "updated"

    parent = APIRouter()
    parent.include_router(router, prefix="/current")
    parent.include_router(router, prefix="/archive")
    report = Composition(features=[Feature(name="projects", routers=[parent])]).check(
        FastAPI()
    )
    assert report.has_errors
    (issue,) = report.issues
    assert issue.message == "Duplicate operation ID 'projects.events'"
    assert issue.locations == ("GET /current/events", "GET /archive/events")


def test_explicit_and_automatic_operation_ids_share_the_collision_check() -> None:
    router = APIRouter(namespace="projects")

    @router.get("/current")
    def events() -> str:
        return "updated"

    @router.sse("/events", operation_id="projects.events")
    async def subscription() -> AsyncIterator[str]:
        yield "updated"

    report = Composition(features=[Feature(name="projects", routers=[router])]).check(
        FastAPI()
    )
    assert report.has_errors
    (issue,) = report.issues
    assert issue.message == "Duplicate operation ID 'projects.events'"
    assert issue.locations == ("GET /current", "GET /events")


def test_multi_method_declarations_require_distinct_operation_ids() -> None:
    router = APIRouter(namespace="projects")

    @router.api_route("/current", methods=["GET", "POST"])
    def current() -> str:
        return "current"

    composition = Composition(features=[Feature(name="projects", routers=[router])])
    report = composition.check(FastAPI())
    assert report.has_errors
    (issue,) = report.issues
    assert issue.code == "canon.routing.E002"
    assert issue.locations == ("GET /current", "POST /current")
