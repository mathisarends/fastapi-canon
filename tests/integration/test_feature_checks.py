import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from dishka import Provider, Scope, provide
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_canon import (
    CheckContext,
    CheckIssue,
    Composition,
    Feature,
    FeatureConfigurationError,
)


class MissingService:
    pass


class BrokenProvider(Provider):
    @provide(scope=Scope.APP)
    def service(self, missing: MissingService) -> str:
        return str(missing)


class LazyProvider(Provider):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    @provide(scope=Scope.APP)
    async def resource(self) -> AsyncIterator[str]:
        self.events.append("resource.start")
        yield "ready"
        self.events.append("resource.stop")


def test_check_collects_independent_failures_without_installing() -> None:
    shared = APIRouter()
    app = FastAPI()
    routes = tuple(app.routes)
    handlers = dict(app.exception_handlers)
    lifespan = app.router.lifespan_context
    composition = Composition(
        features=(
            Feature(name="projects", routers=[shared], providers=[BrokenProvider]),
            Feature(name="archive", routers=[shared]),
        )
    )

    report = composition.check(app)

    assert {issue.code for issue in report.issues} == {
        "canon.routing.E001",
        "canon.providers.E001",
    }
    assert report.has_errors
    assert not report.complete
    provider_issue = next(
        issue for issue in report.issues if issue.code == "canon.providers.E001"
    )
    assert provider_issue.feature_names == ("projects",)
    assert provider_issue.locations == (
        f"{BrokenProvider.__module__}.{BrokenProvider.__qualname__}",
    )
    assert ("http.contracts", "skipped") in {
        (check.name, check.status) for check in report.checks
    }
    assert tuple(app.routes) == routes
    assert app.exception_handlers == handlers
    assert app.router.lifespan_context is lifespan
    assert app.openapi_schema is None
    assert not hasattr(app.state, "dishka_container")
    with pytest.raises(FeatureConfigurationError, match="same router"):
        composition.build(app)
    assert tuple(app.routes) == routes


def test_check_materializes_factories_without_starting_resources() -> None:
    events: list[str] = []

    def provider_factory() -> Provider:
        events.append("factory")
        return LazyProvider(events)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        events.append("lifespan.start")
        yield
        events.append("lifespan.stop")

    composition = Composition(
        features=(
            Feature(name="projects", providers=[provider_factory], lifespan=lifespan),
        )
    )
    app = FastAPI()

    report = composition.check(app)
    assert report.complete and not report.has_errors
    assert events == ["factory"]

    composition.build(app)
    assert events == ["factory", "factory"]
    assert composition.check(app).complete
    assert events == ["factory", "factory"]

    async def run_lifespan() -> None:
        async with asyncio.timeout(5), app.router.lifespan_context(app):
            assert events == ["factory", "factory", "lifespan.start"]

    asyncio.run(run_lifespan())
    assert events == ["factory", "factory", "lifespan.start", "lifespan.stop"]


def test_duplicate_operation_ids_name_both_features_and_routes() -> None:
    first = APIRouter()
    second = APIRouter()
    first.get("/projects", operation_id="projects.get")(lambda: "projects")
    second.get("/archive", operation_id="projects.get")(lambda: "archive")
    composition = Composition(
        features=(
            Feature(name="projects", routers=[first]),
            Feature(name="archive", routers=[second]),
        )
    )
    app = FastAPI()

    report = composition.check(app)

    assert report.has_errors
    (issue,) = report.issues
    assert issue.code == "canon.routing.E002"
    assert issue.feature_names == ("projects", "archive")
    assert issue.locations == ("GET /projects", "GET /archive")
    assert "operation_id" in issue.hint
    with pytest.raises(FeatureConfigurationError, match="Duplicate operation ID"):
        composition.build(app)
    assert not hasattr(app.state, "dishka_container")


def test_feature_checks_receive_detached_context_and_run_during_validation() -> None:
    calls: list[str] = []

    def check_exports(context: CheckContext) -> list[CheckIssue]:
        calls.append(context.feature.name)
        assert context.composition.features == (context.feature,)
        return [
            CheckIssue(
                "reports.W001",
                "No export operation configured",
                "Add an export operation.",
                severity="warning",
            )
        ]

    checks = [check_exports]
    feature = Feature(name="reports", checks=checks)
    checks.clear()
    composition = Composition(features=(feature,))
    app = FastAPI()

    report = composition.check(app)
    assert report.complete and not report.has_errors
    assert report.issues[0].feature_names == ("reports",)
    composition.build(app)
    assert calls == ["reports", "reports", "reports"]


def test_broken_feature_check_is_incomplete_and_blocks_installation() -> None:
    def broken(context: CheckContext) -> list[CheckIssue]:
        raise RuntimeError("secret must not appear in diagnostics")

    composition = Composition(features=(Feature(name="reports", checks=[broken]),))
    app = FastAPI()
    report = composition.check(app)

    assert report.has_errors and not report.complete
    assert report.issues[0].code == "canon.checks.E001"
    assert report.checks[-1].status == "incomplete"
    assert "secret" not in report.issues[0].message
    with pytest.raises(FeatureConfigurationError, match="did not complete"):
        composition.build(app)


def test_check_on_another_installed_composition_reports_conflict() -> None:
    app = Composition(features=(Feature(name="first"),)).build(FastAPI())
    report = Composition(features=(Feature(name="second"),)).check(app)
    assert report.has_errors
    assert report.issues[0].code == "canon.composition.E001"
    assert report.checks[0].status == "failed"


def test_check_includes_existing_application_operation_ids() -> None:
    app = FastAPI()
    app.get("/existing", operation_id="projects.get")(lambda: "existing")
    router = APIRouter()
    router.get("/projects", operation_id="projects.get")(lambda: "projects")
    composition = Composition(features=(Feature(name="projects", routers=[router]),))
    report = composition.check(app)
    (issue,) = report.issues
    assert issue.code == "canon.routing.E002"
    assert issue.feature_names == ("<application>", "projects")
    assert issue.locations == ("GET /existing", "GET /projects")


def test_async_feature_checks_are_rejected_before_they_create_coroutines() -> None:
    async def check(context: CheckContext) -> list[CheckIssue]:
        return []

    with pytest.raises(FeatureConfigurationError, match="synchronous"):
        Feature(name="reports", checks=[check])  # type: ignore[list-item]  # Verify invalid runtime input.


def test_check_detects_operation_ids_added_after_installation() -> None:
    router = APIRouter()
    router.get("/projects", operation_id="projects.get")(lambda: "projects")
    composition = Composition(features=(Feature(name="projects", routers=[router]),))
    app = composition.build(FastAPI())
    schema = app.openapi_schema
    app.get("/later", operation_id="projects.get")(lambda: "later")
    report = composition.check(app)
    assert report.has_errors and not report.complete
    assert report.issues[0].code == "canon.routing.E002"
    assert report.issues[0].locations == ("GET /projects", "GET /later")
    assert app.openapi_schema is schema
    with pytest.raises(FeatureConfigurationError, match="Duplicate operation ID"):
        composition.build(app)


@pytest.mark.parametrize(
    ("first_path", "second_path"),
    [
        ("/same", "/same"),
        ("/items/{item_id}", "/items/{other_id}"),
        ("/items/{item_id:str}", "/items/{other_id}"),
        ("/items/{item_id:int}", "/items/{other_id:int}"),
    ],
    ids=["literal", "parameters", "default-converter", "typed-converter"],
)
@pytest.mark.parametrize("existing", [False, True], ids=["features", "application"])
def test_http_path_collisions_reject_before_installation(
    first_path: str, second_path: str, existing: bool
) -> None:
    first = APIRouter(prefix="/v1")
    second = APIRouter()
    # Schema visibility and operation IDs do not change HTTP dispatch.
    first.get(first_path, operation_id="first", include_in_schema=False)(lambda: "one")
    second.get(second_path, operation_id="second")(lambda: "two")
    nested = APIRouter(prefix="/v1")
    nested.include_router(second)
    app = FastAPI()
    features = [Feature(name="two", routers=[nested])]
    if existing:
        app.include_router(first)
    else:
        features.insert(0, Feature(name="one", routers=[first]))
    composition = Composition(features=features)
    routes = tuple(app.routes)
    handlers = dict(app.exception_handlers)
    lifespan = app.router.lifespan_context

    report = composition.check(app)

    [issue] = report.issues
    assert issue.code == "canon.routing.E003"
    assert issue.feature_names == ("<application>" if existing else "one", "two")
    assert issue.locations == (
        "GET /v1" + first_path.replace(":str", "").replace(":int", ""),
        "GET /v1" + second_path.replace(":int", ""),
    )
    assert report.has_errors
    assert ("http.paths", "failed") in {
        (check.name, check.status) for check in report.checks
    }
    with pytest.raises(FeatureConfigurationError, match="Ambiguous HTTP route"):
        composition.build(app)
    assert tuple(app.routes) == routes
    assert app.exception_handlers == handlers
    assert app.router.lifespan_context is lifespan
    assert app.openapi_schema is None
    assert not hasattr(app.state, "dishka_container")


def test_distinct_http_routes_remain_reachable() -> None:
    router = APIRouter(prefix="/items")
    router.get("/fixed", operation_id="fixed")(lambda: "fixed")
    router.get("/{item_id:int}", operation_id="integer")(lambda: "integer")
    router.get("/{other_id}", operation_id="string")(lambda: "string")
    router.post("/fixed", operation_id="create")(lambda: "created")
    other = APIRouter(prefix="/other")
    other.get("/fixed", operation_id="other")(lambda: "other")
    composition = Composition(
        features=[Feature(name="items", routers=[router, other])],
        router_factory=lambda: APIRouter(prefix="/api"),
    )
    app = composition.build(FastAPI())

    async def exercise() -> None:
        async with (
            asyncio.timeout(5),
            AsyncClient(
                transport=ASGITransport(app), base_url="http://test", timeout=5
            ) as client,
        ):
            assert (await client.get("/api/items/fixed")).json() == "fixed"
            assert (await client.get("/api/items/42")).json() == "integer"
            assert (await client.get("/api/items/name")).json() == "string"
            assert (await client.post("/api/items/fixed")).json() == "created"
            assert (await client.get("/api/other/fixed")).json() == "other"

    asyncio.run(exercise())
    assert not composition.check(app).has_errors


def test_http_path_check_includes_composition_router_prefix() -> None:
    router = APIRouter()
    router.get("/same", operation_id="feature")(lambda: "feature")
    composition = Composition(
        features=[Feature(name="feature", routers=[router])],
        router_factory=lambda: APIRouter(prefix="/api"),
    )
    app = FastAPI()
    app.get("/api/same", operation_id="existing")(lambda: "existing")

    [issue] = composition.check(app).issues

    assert issue.code == "canon.routing.E003"
    assert issue.locations == ("GET /api/same", "GET /api/same")


def test_http_path_check_runs_after_installation() -> None:
    router = APIRouter()
    router.get("/same", operation_id="first")(lambda: "first")
    composition = Composition(features=[Feature(name="first", routers=[router])])
    app = composition.build(FastAPI())
    schema = app.openapi_schema
    app.get("/same", operation_id="later", include_in_schema=False)(lambda: "later")

    [issue] = composition.check(app).issues

    assert issue.code == "canon.routing.E003"
    assert issue.feature_names == ("first", "<application>")
    assert app.openapi_schema is schema
    with pytest.raises(FeatureConfigurationError, match="Ambiguous HTTP route"):
        composition.build(app)
