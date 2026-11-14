from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest
from dishka import AsyncContainer, DependencyKey, Provider, Scope
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from fastapi_canon import Composition, Feature, export_contracts
from fastapi_canon.diagnostics import CheckIssue, CheckResult
from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integration import (
    Installer,
    Integration,
    IntegrationContext,
    IntegrationError,
    IntegrationRoute,
    RouteContext,
    attach_route,
)


class Names(tuple[str, ...]):
    pass


@dataclass(frozen=True, slots=True)
class Greetings(Integration):
    path: str = "/greetings"

    def providers(
        self, parts: tuple[tuple[str, Any], ...], providers: tuple[Provider, ...]
    ) -> Sequence[Provider]:
        names = Names(f"{feature}:{part.name}" for feature, part in parts)
        provider = Provider(scope=Scope.APP)
        provider.provide(lambda: names, provides=Names)
        return (provider,)

    def prepare(self, context: IntegrationContext) -> Installer:
        if any(
            getattr(route, "path", None) == self.path
            for router in context.routers
            for route in router.routes
        ):
            raise IntegrationError(
                "example.E001",
                f"A feature route shadows {self.path}",
                "Choose another Greetings(path=...).",
            )

        def install(app: FastAPI, container: AsyncContainer | None) -> None:
            assert container is not None

            @app.get(self.path)
            async def greetings() -> list[str]:
                return list(await container.get(Names))

        return Installer(install)


@dataclass(frozen=True, slots=True)
class Greeting:
    integration: ClassVar[type[Integration]] = Greetings
    name: str


def test_enabled_integration_receives_feature_parts_providers_and_installer() -> None:
    app = Composition(
        features=[
            Feature(name="first", integrations=[Greeting("ada")]),
            Feature(name="second", integrations=[Greeting("grace")]),
        ],
        integrations=[Greetings()],
    ).build(FastAPI())

    with TestClient(app) as client:
        assert client.get("/greetings").json() == ["first:ada", "second:grace"]


def _shadowing_feature() -> Feature:
    router = APIRouter()

    @router.get("/greetings")
    async def shadow() -> str:
        return "shadow"

    return Feature(name="shadow", routers=[router])


@pytest.mark.parametrize(
    ("features", "integrations", "code"),
    [
        (
            [Feature(name="first", integrations=[Greeting("ada")])],
            [],
            "canon.integrations.E001",
        ),
        ([_shadowing_feature()], [Greetings()], "example.E001"),
    ],
    ids=["part-without-integration", "integration-owned-code"],
)
def test_integration_failures_are_reported_before_installation(
    features: list[Feature], integrations: list[Integration], code: str
) -> None:
    app = FastAPI()
    composition = Composition(features=features, integrations=integrations)

    report = composition.check(app)

    assert [issue.code for issue in report.issues] == [code]
    with pytest.raises(FeatureConfigurationError, match=code):
        composition.build(app)
    assert "/greetings" not in {getattr(route, "path", None) for route in app.routes}


def test_an_integration_type_is_enabled_once() -> None:
    with pytest.raises(FeatureConfigurationError, match="enables Greetings again"):
        Composition(integrations=[Greetings(), Greetings(path="/other")])


@dataclass(frozen=True, slots=True)
class _Exporter:
    kind: str = "example"

    def export(
        self, app: FastAPI, routes: Sequence[tuple[str, Any]], *, base_url: str
    ) -> dict[str, Any] | None:
        return {"paths": [path for path, _ in routes], "base": base_url}


class _ExampleRoute(IntegrationRoute):
    __slots__ = ("required",)

    def __init__(self, required: type) -> None:
        self.required = required

    @property
    def contract(self) -> _Exporter:
        return _Exporter()

    def check(self, context: RouteContext) -> None:
        name = f"example.dependencies:{context.feature}:{context.path}"
        if context.available(DependencyKey(self.required, ""), Scope.APP):
            context.record(CheckResult(name=name, status="passed"))
            return
        context.report(
            CheckIssue(
                code="example.E002",
                message=f"{context.path} needs {self.required.__name__}",
                hint="Bind it at APP scope.",
                severity="error",
            )
        )


def _route_feature() -> Feature:
    router = APIRouter(prefix="/example")

    async def endpoint() -> str:
        return "ok"

    attach_route(endpoint, _ExampleRoute(Names))
    router.add_api_route("/route", endpoint, methods=["GET"])
    return Feature(name="routes", routers=[router])


def test_attached_route_contributes_its_contract_and_dependency_check() -> None:
    names = Provider(scope=Scope.APP)
    names.provide(lambda: Names(), provides=Names)
    satisfied = Composition(
        features=[_route_feature(), Feature(name="names", providers=[names])]
    )

    missing = Composition(features=[_route_feature()]).check(FastAPI())
    report = satisfied.check(FastAPI())
    app = satisfied.build(FastAPI())

    assert [issue.code for issue in missing.issues] == ["example.E002"]
    assert any(
        result.name == "example.dependencies:routes:/example/route"
        for result in report.checks
    )
    document = export_contracts(app, kinds=["example"], base_url="https://api.test")
    assert document.documents["example"] == {
        "paths": ["/example/route"],
        "base": "https://api.test",
    }
