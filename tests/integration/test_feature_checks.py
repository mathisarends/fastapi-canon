import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from dishka import Provider, Scope, provide
from fastapi import APIRouter, FastAPI

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
    assert ("http.contracts", "skipped") in {
        (check.name, check.status) for check in report.checks
    }
    assert tuple(app.routes) == routes
    assert app.exception_handlers == handlers
    assert app.router.lifespan_context is lifespan
    assert app.openapi_schema is None
    assert not hasattr(app.state, "dishka_container")
    with pytest.raises(FeatureConfigurationError, match="same router"):
        composition.apply(app)
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

    composition.apply(app)
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
        composition.apply(app)
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
    composition.apply(app)
    composition.validate(app)
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
        composition.apply(app)


def test_check_on_another_installed_composition_reports_conflict() -> None:
    app = Composition(features=(Feature(name="first"),)).apply(FastAPI())
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
