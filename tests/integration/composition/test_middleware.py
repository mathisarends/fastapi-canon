import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from typing import Any, ClassVar

import pytest
from dishka import Provider, Scope, provide
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Send
from starlette.types import Scope as ASGIScope

from fastapi_canon import APIRouter, Composition, Feature, Inject
from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry import Telemetry


class Routes:
    pass


class Unbound:
    pass


class Broken:
    pass


class Resource:
    pass


class RoutesProvider(Provider):
    scope = Scope.APP

    @provide
    def routes(self) -> Routes:
        return Routes()


class Recording:
    def __init__(self, app: ASGIApp, name: str, log: list[str]) -> None:
        self.app = app
        self.name = name
        self.log = log

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        self.log.append(f"{scope['type']}:{self.name}")
        await self.app(scope, receive, send)


class InjectedRecording:
    created: ClassVar[list["InjectedRecording"]] = []

    def __init__(
        self, app: ASGIApp, routes: "Inject[Routes]", seen: list[Routes]
    ) -> None:
        self.app = app
        self.routes = routes
        self.seen = seen
        type(self).created.append(self)

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        self.seen.append(self.routes)
        await self.app(scope, receive, send)


class InjectedHttp(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, routes: Inject[Routes]) -> None:
        super().__init__(app)
        self.routes = routes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["x-routes"] = type(self.routes).__name__
        return response


class NeedsUnbound:
    def __init__(self, app: ASGIApp, service: Inject[Unbound]) -> None:
        self.app = app

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)


class NeedsBroken:
    def __init__(
        self, app: ASGIApp, resource: Inject[Resource], broken: Inject[Broken]
    ) -> None:
        self.app = app

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)


@pytest.fixture(autouse=True)
def reset_created() -> None:
    InjectedRecording.created.clear()


def _declared(middleware: Any, **options: Any) -> Middleware:
    # Starlette's ParamSpec cannot express parameters that Canon injects.
    return Middleware(middleware, **options)


def _feature(*providers: type[Provider] | Provider) -> Feature:
    router = APIRouter()

    @router.get("/ping")
    async def ping() -> str:
        return "pong"

    @router.websocket("/socket")
    async def socket(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.close()

    return Feature(name="ping", routers=[router], providers=providers)


def test_declared_order_is_outermost_first_around_earlier_middleware() -> None:
    log: list[str] = []
    app = FastAPI()
    app.add_middleware(Recording, name="before", log=log)
    composition = Composition(
        features=[_feature(RoutesProvider)],
        middleware=[
            Middleware(Recording, name="first", log=log),
            Middleware(Recording, name="second", log=log),
        ],
        outer_middleware=[
            Middleware(Recording, name="outer-first", log=log),
            Middleware(Recording, name="outer-second", log=log),
        ],
    )
    composition.build(app)
    composition.build(app)
    app.add_middleware(Recording, name="after", log=log)

    assert TestClient(app).get("/ping").status_code == 200

    assert log == [
        "http:outer-first",
        "http:outer-second",
        "http:after",
        "http:first",
        "http:second",
        "http:before",
    ]


def test_outer_cors_applies_to_application_body_limit_problems() -> None:
    origin = "https://app.example.test"
    app = FastAPI(max_body_size=8)

    @app.post("/upload")
    async def upload(body: dict[str, str]) -> dict[str, str]:
        return body

    composed = Composition(
        outer_middleware=[
            Middleware(CORSMiddleware, allow_origins=[origin], allow_methods=["POST"]),
        ]
    ).build(app)

    assert composed is app
    with TestClient(composed) as client:
        preflight = client.options(
            "/upload",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        rejected = client.post(
            "/upload", headers={"Origin": origin}, json={"name": "Ada"}
        )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert rejected.status_code == 413
    assert rejected.headers["access-control-allow-origin"] == origin
    assert rejected.headers["content-type"] == "application/problem+json"
    assert rejected.json()["code"] == "http_413"


def test_outer_middleware_is_part_of_the_installed_composition() -> None:
    entry = Middleware(Recording, name="outer", log=[])
    composition = Composition(outer_middleware=[entry])
    app = composition.build(FastAPI())
    changed = replace(composition, outer_middleware=())

    assert [issue.code for issue in changed.check(app).issues] == [
        "canon.composition.E001"
    ]
    with pytest.raises(FeatureConfigurationError, match="different composition"):
        changed.build(app)


def test_injected_service_is_shared_by_one_instance_without_lifespan() -> None:
    seen: list[Routes] = []
    app = Composition(
        features=[_feature(RoutesProvider)],
        middleware=[_declared(InjectedRecording, seen=seen)],
    ).build(FastAPI())
    client = TestClient(app)

    client.get("/ping")
    client.get("/ping")

    assert len(InjectedRecording.created) == 1
    assert len(seen) == 2
    assert seen[0] is seen[1]


@pytest.mark.parametrize("placement", ["middleware", "outer_middleware"])
async def test_concurrent_first_calls_create_one_instance(placement: str) -> None:
    seen: list[Routes] = []
    app = Composition(
        features=[_feature(RoutesProvider)],
        **{placement: [_declared(InjectedRecording, seen=seen)]},
    ).build(FastAPI())

    async def request() -> None:
        scope: ASGIScope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/ping",
            "raw_path": b"/ping",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
        }

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message: Message) -> None:
            return None

        await app(scope, receive, send)

    async with asyncio.timeout(5):
        await asyncio.gather(*(request() for _ in range(5)))

    assert len(InjectedRecording.created) == 1
    assert len(seen) == 5


def test_base_http_middleware_receives_injected_service() -> None:
    app = Composition(
        features=[_feature(RoutesProvider)],
        middleware=[_declared(InjectedHttp)],
    ).build(FastAPI())

    with TestClient(app) as client:
        response = client.get("/ping")

    assert response.headers["x-routes"] == "Routes"


def test_websocket_and_lifespan_scopes_pass_through() -> None:
    log: list[str] = []
    app = Composition(
        features=[_feature(RoutesProvider)],
        middleware=[Middleware(Recording, name="outer", log=log)],
    ).build(FastAPI())

    with TestClient(app) as client, client.websocket_connect("/socket"):
        pass

    assert log == ["lifespan:outer", "websocket:outer"]


@pytest.mark.parametrize("placement", ["middleware", "outer_middleware"])
def test_missing_binding_is_a_preflight_issue_and_fails_build(placement: str) -> None:
    composition = Composition(
        features=[_feature(RoutesProvider)],
        **{placement: [_declared(NeedsUnbound)]},
    )

    report = composition.check(FastAPI())

    assert [issue.code for issue in report.issues] == ["canon.providers.E007"]
    with pytest.raises(FeatureConfigurationError, match=r"canon\.providers\.E007"):
        composition.build(FastAPI())


def test_injected_middleware_without_a_container_fails_build() -> None:
    composition = Composition(middleware=[_declared(NeedsUnbound)])

    with pytest.raises(FeatureConfigurationError, match=r"canon\.providers\.E007"):
        composition.build(FastAPI())


def test_explicit_argument_for_an_injected_parameter_is_rejected() -> None:
    composition = Composition(
        features=[_feature(RoutesProvider)],
        middleware=[_declared(InjectedHttp, routes=Routes())],
    )

    with pytest.raises(FeatureConfigurationError, match="routes"):
        composition.build(FastAPI())


@pytest.mark.parametrize("placement", ["middleware", "outer_middleware"])
def test_startup_failure_unwinds_the_composed_lifespan(placement: str) -> None:
    events: list[str] = []

    async def resource() -> AsyncIterator[Resource]:
        yield Resource()
        events.append("closed")

    def broken() -> Broken:
        raise RuntimeError("cannot create service")

    provider = Provider(scope=Scope.APP)
    provider.provide(resource)
    provider.provide(broken)
    app = Composition(
        features=[_feature(provider)],
        integrations=[
            replace(Telemetry.disabled(), shutdown=lambda: events.append("shutdown"))
        ],
        **{placement: [_declared(NeedsBroken)]},
    ).build(FastAPI())

    with pytest.raises(RuntimeError, match="cannot create service"), TestClient(app):
        pass

    assert events == ["closed", "shutdown"]


@pytest.mark.parametrize("entries", [["invalid"], "duplicate"])
def test_constructor_rejects_invalid_entries(entries: object) -> None:
    middleware = Middleware(Recording, name="once", log=[])
    values = [middleware, middleware] if entries == "duplicate" else entries

    with pytest.raises(FeatureConfigurationError, match="middleware"):
        Composition(middleware=values)  # type: ignore[arg-type]  # Invalid on purpose.


def test_override_and_replace_keep_declared_middleware() -> None:
    entry = Middleware(Recording, name="kept", log=[])
    outer = Middleware(Recording, name="outer", log=[])
    composition = Composition(
        features=[_feature(RoutesProvider)],
        middleware=[entry],
        outer_middleware=[outer],
    ).override(RoutesProvider)

    assert composition.middleware == (entry,)
    assert composition.outer_middleware == (outer,)
    assert replace(composition, request_logging=True).middleware == (entry,)
    assert replace(composition, request_logging=True).outer_middleware == (outer,)
