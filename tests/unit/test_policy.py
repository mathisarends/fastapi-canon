from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Annotated, Any

import pytest
from dishka import Provider, Scope, provide
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, FastAPI, Header
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from fastapi_canon import APIRouter as CanonAPIRouter
from fastapi_canon import (
    CanonResponse,
    Composition,
    Error,
    ErrorOptions,
    Feature,
    RouterPolicy,
    inspect_app,
    raises,
)
from fastapi_canon.error import ErrorConfigurationError, ErrorRegistry
from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.policy import dependency_errors
from fastapi_canon.router import CanonRouter


class AuthenticationRequired(Exception):
    pass


class Forbidden(Exception):
    pass


class MissingItem(Exception):
    pass


AUTHENTICATION_REQUIRED = Error(
    AuthenticationRequired,
    status=401,
    code="authentication_required",
    title="Authentication required",
    headers={"WWW-Authenticate": "Bearer"},
    openapi_headers={"WWW-Authenticate": {"schema": {"type": "string"}}},
)
FORBIDDEN = Error(Forbidden, status=403, code="forbidden", title="Forbidden")
MISSING = Error(MissingItem, status=403, code="missing_item", title="Missing item")
PROBLEMS = ErrorOptions(type_base="https://example.test/problems")


async def require_user(authorization: str | None = Header(default=None)) -> None:
    if authorization != "Bearer test":
        raise AuthenticationRequired


PROTECTED = RouterPolicy(
    dependencies=[Depends(require_user)],
    raises=[AUTHENTICATION_REQUIRED, FORBIDDEN],
)


def test_policy_copies_sequences_and_is_frozen() -> None:
    dependencies = [Depends(require_user)]
    errors = [AUTHENTICATION_REQUIRED]
    policy = RouterPolicy(dependencies=dependencies, raises=errors)
    dependencies.clear()
    errors.clear()
    assert len(policy.dependencies) == 1
    assert policy.raises == (AUTHENTICATION_REQUIRED,)
    with pytest.raises(FrozenInstanceError):
        policy.raises = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"raises": "invalid"}, "must be a sequence"),
        ({"dependencies": None}, "must be a sequence"),
        ({"dependencies": "invalid"}, "must be a sequence"),
        ({"dependencies": [object()]}, "Depends instance"),
        ({"raises": [object()]}, "Error instance"),
    ],
)
def test_policy_rejects_invalid_configuration(
    options: dict[str, Any], message: str
) -> None:
    with pytest.raises(ErrorConfigurationError, match=message):
        RouterPolicy(**options)


def test_router_policy_combines_contracts_dependencies_and_dishka_injection() -> None:
    calls: list[str] = []

    async def local_dependency() -> None:
        calls.append("local")

    class Services(Provider):
        @provide(scope=Scope.REQUEST)
        def value(self) -> str:
            return "injected"

    router = CanonRouter(
        policy=PROTECTED,
        raises=[FORBIDDEN],
        dependencies=[Depends(local_dependency)],
        route_class=DishkaRoute,
    )

    @router.get("/items", raises=[MISSING], response=CanonResponse.json())
    async def items(value: FromDishka[str]) -> str:
        return value

    feature = Feature(name="items", routers=[router], providers=[Services()])
    composition = Composition(features=(feature,), errors=PROBLEMS)
    app = composition.build(FastAPI())
    with TestClient(app) as client:
        response = client.get("/items")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["code"] == "authentication_required"
        assert calls == []
        assert (
            client.get("/items", headers={"Authorization": "Bearer test"}).json()
            == "injected"
        )
    assert calls == ["local"]
    responses = app.openapi()["paths"]["/items"]["get"]["responses"]
    assert set(responses) >= {"200", "401", "403"}
    assert set(
        responses["403"]["content"]["application/problem+json"]["schema"][
            "discriminator"
        ]["mapping"]
    ) == {
        "https://example.test/problems/forbidden",
        "https://example.test/problems/missing_item",
    }
    assert router.policy is PROTECTED
    assert router.raises == (AUTHENTICATION_REQUIRED, FORBIDDEN)
    assert MISSING in feature.errors


def test_router_factory_policy_extends_nested_feature_routers() -> None:
    child = CanonRouter()

    @child.get("/items/{item_id}", raises=[MISSING])
    async def item(item_id: int) -> int:
        if item_id == 0:
            raise MissingItem
        return item_id

    @child.get("/hidden", include_in_schema=False)
    async def hidden() -> None:
        return None

    route = child.routes[0]
    assert isinstance(route, APIRoute)
    original_responses = deepcopy(route.responses)
    parent = APIRouter()
    parent.include_router(child)
    feature = Feature(name="items", routers=[parent])
    policy = RouterPolicy(dependencies=PROTECTED.dependencies, raises=PROTECTED.raises)
    composition = Composition(
        features=(feature,),
        router_factory=lambda: CanonRouter(prefix="/api", policy=policy),
    )
    protected = FastAPI()
    protected.get("/health")(lambda: "ok")
    composition.build(protected)
    protected.get("/later")(lambda: "later")
    composition.build(protected)
    public = Composition(features=(feature,)).build(FastAPI())

    with TestClient(protected) as client:
        assert client.get("/api/items/1").status_code == 401
        assert client.get("/api/hidden").status_code == 401
        assert client.get("/health").status_code == 200
        assert client.get("/later").status_code == 200
        assert (
            client.get("/api/items/0", headers={"Authorization": "Bearer test"}).json()[
                "code"
            ]
            == "missing_item"
        )
    assert TestClient(public).get("/items/1").status_code == 200
    protected_schema = protected.openapi()
    responses = protected_schema["paths"]["/api/items/{item_id}"]["get"]["responses"]
    assert "401" in responses
    assert (
        len(responses["403"]["content"]["application/problem+json"]["schema"]["oneOf"])
        == 2
    )
    assert "WWW-Authenticate" in responses["401"]["headers"]
    assert "401" not in protected_schema["paths"]["/health"]["get"]["responses"]
    assert "401" not in protected_schema["paths"]["/later"]["get"]["responses"]
    assert (
        "401" not in public.openapi()["paths"]["/items/{item_id}"]["get"]["responses"]
    )
    assert "x-fastapi-canon" not in str(protected_schema)
    assert route.responses == original_responses
    assert child.dependencies == []
    assert child.raises == ()


def test_invalid_policy_type_is_rejected() -> None:
    with pytest.raises(ErrorConfigurationError, match="RouterPolicy instance"):
        CanonRouter(policy=object())  # type: ignore[arg-type]


@pytest.mark.parametrize("scope", ["router", "composition"])
def test_policy_registry_collisions_are_rejected(scope: str) -> None:
    other = Error(Forbidden, status=403, code="other_forbidden", title="Other")
    app = FastAPI()
    original_routes = tuple(app.routes)
    with pytest.raises(ErrorConfigurationError, match="exception"):
        if scope == "router":
            CanonRouter(policy=PROTECTED, raises=[other])
        else:
            Composition(
                features=(Feature(name="other", errors=[other]),),
                router_factory=lambda: CanonRouter(policy=PROTECTED),
            ).build(app)
    assert tuple(app.routes) == original_routes


def test_inherited_status_rejects_manual_collision_before_installation() -> None:
    router = CanonRouter()
    router.get("/items", responses={401: {"description": "Manual error"}})(lambda: None)
    app = FastAPI()
    original_routes = tuple(app.routes)
    with pytest.raises(FeatureConfigurationError, match="inherits response status 401"):
        Composition(
            features=(Feature(name="items", routers=[router]),),
            router_factory=lambda: CanonRouter(policy=PROTECTED),
        ).build(app)
    assert tuple(app.routes) == original_routes
    assert app.openapi_schema is None


def test_dependency_only_policy_preserves_normalizers_and_deduplicates_execution() -> (
    None
):
    calls: list[str] = []

    async def prerequisite() -> None:
        calls.append("called")

    policy = RouterPolicy(dependencies=[Depends(prerequisite)])
    router = CanonRouter(policy=policy)
    router.get("/items")(lambda: None)
    app = Composition(
        features=(Feature(name="items", routers=[router]),),
        router_factory=lambda: CanonRouter(policy=policy),
    ).build(FastAPI())
    assert TestClient(app).get("/items").status_code == 200
    assert calls == ["called"]
    assert app.openapi()["paths"]["/items"]["get"]["responses"]["500"]["content"][
        "application/problem+json"
    ]["schema"] == {"$ref": "#/components/schemas/InternalServerProblem"}


def test_parent_raises_extend_included_operations() -> None:
    parent = CanonRouter(raises=[FORBIDDEN])
    child = CanonRouter()
    child.get("/child", raises=[MISSING])(lambda: None)
    sibling = APIRouter()
    sibling.get("/sibling")(lambda: None)
    parent.include_router(child)
    app = Composition(
        features=(Feature(name="parent", routers=[parent, sibling]),)
    ).build(FastAPI())
    document = app.openapi()
    schema = document["paths"]["/child"]["get"]["responses"]["403"]["content"][
        "application/problem+json"
    ]["schema"]
    assert schema["oneOf"] == [
        {"$ref": "#/components/schemas/ForbiddenProblem"},
        {"$ref": "#/components/schemas/MissingItemProblem"},
    ]
    assert "callbacks" not in document["paths"]["/child"]["get"]
    assert "403" not in document["paths"]["/sibling"]["get"]["responses"]


def test_parent_raises_extend_operations_included_without_composition() -> None:
    parent = CanonRouter(raises=[FORBIDDEN])
    child = APIRouter()
    child.get("/child")(lambda: None)
    parent.include_router(child)
    app = FastAPI()
    app.include_router(parent)
    # Manual assembly without Composition installs a registry directly.
    ErrorRegistry(errors=[FORBIDDEN], type_base=PROBLEMS.type_base).install(app)
    responses = app.openapi()["paths"]["/child"]["get"]["responses"]
    assert responses["403"]["content"]["application/problem+json"]["schema"] == {
        "$ref": "#/components/schemas/ForbiddenProblem"
    }


def test_inherited_422_includes_automatic_validation() -> None:
    invalid = Error(ValueError, status=422, code="invalid_input", title="Invalid input")
    policy = RouterPolicy(raises=[invalid])
    router = CanonRouter()

    @router.get("/items/{item_id}")
    async def item(item_id: int) -> int:
        return item_id

    app = Composition(
        features=(Feature(name="items", routers=[router]),),
        router_factory=lambda: CanonRouter(policy=policy),
    ).build(FastAPI())
    response = app.openapi()["paths"]["/items/{item_id}"]["get"]["responses"]["422"]
    assert response["content"]["application/problem+json"]["schema"] == {
        "oneOf": [
            {"$ref": "#/components/schemas/InvalidInputProblem"},
            {"$ref": "#/components/schemas/RequestValidationProblem"},
        ],
        "discriminator": {
            "propertyName": "code",
            "mapping": {
                "invalid_input": "#/components/schemas/InvalidInputProblem",
                "request_validation_error": "#/components/schemas/RequestValidationProblem",
            },
        },
    }


@raises(AUTHENTICATION_REQUIRED)
async def signed_in_user(authorization: str | None = Header(default=None)) -> str:
    if authorization != "Bearer test":
        raise AuthenticationRequired
    return "user"


@raises(FORBIDDEN)
async def administrator(
    user: Annotated[str, Depends(signed_in_user)],
    role: str | None = Header(default=None),
) -> str:
    if role != "admin":
        raise Forbidden
    return user


SignedInUser = Annotated[str, Depends(signed_in_user)]


def _responses(app: FastAPI, path: str, method: str = "get") -> set[str]:
    return set(app.openapi()["paths"][path][method]["responses"])


def test_raises_rejects_missing_or_invalid_errors() -> None:
    with pytest.raises(ErrorConfigurationError, match="at least one Error"):
        raises()
    with pytest.raises(ErrorConfigurationError, match="Error instance"):
        raises("forbidden")  # type: ignore[arg-type]
    with pytest.raises(ErrorConfigurationError, match="callable dependency"):
        raises(FORBIDDEN)("not callable")


def test_dependency_errors_follow_parameters_classes_and_nested_dependencies() -> None:
    @raises(MISSING)
    class Pagination:
        def __init__(self, user: SignedInUser, limit: int = 10) -> None:
            self.limit = limit

    async def endpoint(
        page: Annotated[Pagination, Depends()],
        admin: str = Depends(administrator),
    ) -> None:
        return None

    assert dependency_errors(call=endpoint) == (
        MISSING,
        AUTHENTICATION_REQUIRED,
        FORBIDDEN,
    )
    assert dependency_errors([Depends(administrator)]) == (
        FORBIDDEN,
        AUTHENTICATION_REQUIRED,
    )


def test_parameter_dependency_errors_are_documented_and_handled() -> None:
    router = CanonAPIRouter(namespace="projects")

    @router.get("/me")
    async def me(user: SignedInUser) -> str:
        return user

    @router.get("/admin")
    async def admin(user: Annotated[str, Depends(administrator)]) -> str:
        return user

    app = Composition(
        features=(Feature(name="projects", routers=[router]),), errors=PROBLEMS
    ).build(FastAPI())

    assert {"401"} <= _responses(app, "/me") and "403" not in _responses(app, "/me")
    assert {"401", "403"} <= _responses(app, "/admin")
    client = TestClient(app)
    assert client.get("/me").json()["code"] == "authentication_required"
    denied = client.get("/admin", headers={"Authorization": "Bearer test"})
    assert (denied.status_code, denied.json()["code"]) == (403, "forbidden")
    origins = {
        operation.operation_id: {
            (error.code, error.origin) for error in operation.errors
        }
        for operation in inspect_app(app).operations
    }
    assert origins["projects.admin"] == {
        ("authentication_required", "dependency"),
        ("forbidden", "dependency"),
    }


def test_router_policy_and_inclusion_dependencies_extend_included_operations() -> None:
    def child(namespace: str) -> CanonAPIRouter:
        router = CanonAPIRouter(namespace=namespace)

        @router.get("/items")
        async def items() -> list[int]:
            return []

        return router

    guarded = CanonAPIRouter(prefix="/guarded", dependencies=[Depends(administrator)])
    guarded.include_router(child("guarded"))
    included = CanonAPIRouter(prefix="/included")
    included.include_router(child("included"), dependencies=[Depends(signed_in_user)])
    policy = RouterPolicy(dependencies=[Depends(signed_in_user)])
    with_policy = CanonAPIRouter(prefix="/policy", policy=policy)
    with_policy.include_router(child("policy"))

    app = Composition(
        features=(Feature(name="items", routers=[guarded, included, with_policy]),),
        errors=PROBLEMS,
    ).build(FastAPI())

    assert {"401", "403"} <= _responses(app, "/guarded/items")
    assert "401" in _responses(app, "/included/items")
    assert "401" in _responses(app, "/policy/items")
    assert guarded.raises == (FORBIDDEN, AUTHENTICATION_REQUIRED)
    response = TestClient(app).get("/included/items")
    assert response.json()["code"] == "authentication_required"


def test_explicit_router_raises_keep_their_origin() -> None:
    router = CanonAPIRouter(
        raises=[AUTHENTICATION_REQUIRED], dependencies=[Depends(signed_in_user)]
    )

    @router.get("/me")
    async def me() -> None:
        return None

    app = Composition(features=(Feature(name="me", routers=[router]),)).build(FastAPI())
    (operation,) = inspect_app(app).operations
    assert [(error.code, error.origin) for error in operation.errors] == [
        ("authentication_required", "router")
    ]


def test_stream_admission_dependency_errors_are_documented() -> None:
    router = CanonAPIRouter()

    @router.sse("/events")
    async def events(user: SignedInUser) -> AsyncIterator[str]:
        yield user

    app = Composition(features=(Feature(name="events", routers=[router]),)).build(
        FastAPI()
    )

    assert "401" in _responses(app, "/events")
    response = TestClient(app).get("/events")
    assert (response.status_code, response.json()["code"]) == (
        401,
        "authentication_required",
    )
