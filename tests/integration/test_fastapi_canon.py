"""Contracts that cross the composition, runtime, and OpenAPI boundaries."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message

from fastapi_canon import (
    CanonResponse,
    CanonRouter,
    Composition,
    ConfigurationError,
    Error,
    ErrorConfigurationError,
    ErrorOptions,
    ErrorRegistry,
    Feature,
    FeatureConfigurationError,
    ResponseConfigurationError,
)


class Missing(Exception):
    pass


def test_configuration_errors_have_one_public_base() -> None:
    for error in (
        ErrorConfigurationError,
        FeatureConfigurationError,
        ResponseConfigurationError,
    ):
        assert issubclass(error, ConfigurationError)
        assert issubclass(error, ValueError)


def test_composition_normalizes_without_domain_registries() -> None:
    router = CanonRouter(http_statuses=[401])

    @router.get("/items/{item_id}")
    async def item(item_id: int) -> None:
        raise HTTPException(401, "Sign in")

    @router.get("/broken")
    async def broken() -> None:
        raise RuntimeError("private database credentials")

    app = Composition(features=[Feature(name="items", routers=[router])]).apply(
        FastAPI()
    )
    document = app.openapi()
    validate(document)
    with TestClient(app, raise_server_exceptions=False) as client:
        for path, status, code in (
            ("/items/1", 401, "http_401"),
            ("/items/invalid", 422, "request_validation_error"),
            ("/broken", 500, "internal_server_error"),
        ):
            response = client.get(path)
            assert response.status_code == status
            assert response.headers["content-type"] == "application/problem+json"
            assert response.json()["code"] == code
            assert "credentials" not in response.text
            operation_path = "/broken" if path == "/broken" else "/items/{item_id}"
            _validate_payload(document, operation_path, status, response.json())


def test_nested_routers_and_explicit_registries_contribute_together() -> None:
    missing = Error(Missing, status=404, code="item_missing", title="Item missing")
    registry = ErrorRegistry(errors=[missing])
    child = CanonRouter(error_registry=registry)

    @child.get("/items/{item_id}", raises=[missing])
    async def item(item_id: str) -> None:
        raise Missing(item_id)

    parent = CanonRouter(prefix="/api")
    parent.include_router(child)
    root = CanonRouter()
    root.include_router(parent)
    feature = Feature(name="items", routers=[root], errors=ErrorRegistry(errors=[]))
    app = Composition(features=[feature]).apply(FastAPI())

    with TestClient(app) as client:
        response = client.get("/api/items/missing")
    assert response.status_code == 404
    assert response.json()["type"] == "urn:fastapi-canon:problem/item_missing"
    _validate_payload(app.openapi(), "/api/items/{item_id}", 404, response.json())


@pytest.mark.parametrize(
    "method", ["get", "put", "post", "delete", "options", "head", "patch", "trace"]
)
def test_http_contracts_compose_on_every_decorator(method: str) -> None:
    router = CanonRouter(http_statuses=[401])
    getattr(router, method)("/protected", http_statuses=[401, 403])(lambda: None)
    app = Composition(features=[Feature(name="private", routers=[router])]).apply(
        FastAPI()
    )
    responses = app.openapi()["paths"]["/protected"][method]["responses"]
    assert set(responses) >= {"401", "403", "500"}
    assert "x-fastapi-canon" not in str(responses)


@pytest.mark.parametrize("statuses", [[399], [600], [True], "401"])
def test_invalid_http_contracts_fail_at_declaration(statuses: Any) -> None:
    with pytest.raises(ConfigurationError, match="http_statuses"):
        CanonRouter(http_statuses=statuses)
    with pytest.raises(ConfigurationError, match="http_statuses"):
        CanonRouter().get("/invalid", http_statuses=statuses)


def test_http_contracts_respect_normalization_options() -> None:
    router = CanonRouter(http_statuses=[401])
    router.get("/private")(lambda: None)
    app = Composition(
        features=[Feature(name="private", routers=[router])],
        errors=ErrorOptions(include_http_exceptions=False),
    ).apply(FastAPI())
    with pytest.raises(ConfigurationError, match="normalization is disabled"):
        app.openapi()


@pytest.mark.parametrize("status", [422, 500])
def test_framework_and_domain_errors_share_discriminated_contracts(status: int) -> None:
    missing = Error(Missing, status=status, code="item_missing", title="Item missing")
    router = CanonRouter(error_registry=ErrorRegistry(errors=[missing]))

    @router.get("/items/{item_id}", raises=[missing])
    async def item(item_id: int) -> None:
        if item_id == 0:
            raise RuntimeError("private")
        raise Missing

    app = Composition(features=[Feature(name="items", routers=[router])]).apply(
        FastAPI()
    )
    document = app.openapi()
    validate(document)
    schema = document["paths"]["/items/{item_id}"]["get"]["responses"][str(status)][
        "content"
    ]["application/problem+json"]["schema"]
    builtin_code = (
        "request_validation_error" if status == 422 else "internal_server_error"
    )
    assert set(schema["discriminator"]["mapping"]) == {"item_missing", builtin_code}
    with TestClient(app, raise_server_exceptions=False) as client:
        for path in ("/items/1", "/items/invalid" if status == 422 else "/items/0"):
            response = client.get(path)
            assert response.status_code == status
            _validate_payload(document, "/items/{item_id}", status, response.json())


def test_failed_registry_installation_preserves_handlers_and_openapi() -> None:
    missing = Error(Missing, status=404, code="item_missing", title="Item missing")
    registry = ErrorRegistry(errors=[missing], type_base="urn:test:problems")
    app = FastAPI()

    async def custom_http(request: Request, exception: Exception) -> JSONResponse:
        return JSONResponse({"custom": True})

    app.add_exception_handler(StarletteHTTPException, custom_http)
    original_handlers = app.exception_handlers.copy()
    original_openapi = app.openapi
    with pytest.raises(ConfigurationError, match="already defines"):
        registry.install(app)
    assert app.exception_handlers == original_handlers
    assert app.openapi == original_openapi


def test_registry_reinstallation_requires_matching_options() -> None:
    registry = ErrorRegistry(errors=[], type_base="urn:test:problems")
    app = FastAPI()
    registry.install(app)
    registry.install(app)
    with pytest.raises(ConfigurationError, match="different normalization options"):
        registry.install(app, include_http_exceptions=False)


@pytest.mark.parametrize(
    "parameter, value",
    [("type_base", ""), ("type_base", "/relative"), ("registry_name", "")],
)
def test_application_error_options_validate_at_declaration(
    parameter: str, value: str
) -> None:
    with pytest.raises(ConfigurationError):
        ErrorOptions(**{parameter: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("code", ["request_validation_error", "internal_server_error"])
def test_domain_codes_cannot_shadow_framework_problems(code: str) -> None:
    error = Error(Missing, status=400, code=code, title="Conflicting problem")
    registry = ErrorRegistry(errors=[error], type_base="urn:test:problems")
    app = FastAPI()
    before = app.exception_handlers.copy()
    with pytest.raises(
        ConfigurationError, match="conflicts with enabled normalization"
    ):
        registry.install(app)
    assert app.exception_handlers == before


def test_app_router_and_feature_lifespans_run_and_unwind_together() -> None:
    events: list[str] = []

    def lifecycle(name: str) -> Any:
        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            events.append(f"start {name}")
            yield
            events.append(f"stop {name}")

        return lifespan

    router = CanonRouter(lifespan=lifecycle("router"))
    router.get("/health")(lambda: "ok")
    composition = Composition(
        features=[
            Feature(name="health", routers=[router], lifespan=lifecycle("feature"))
        ]
    )
    app = composition.apply(FastAPI(lifespan=lifecycle("app")))
    with TestClient(app):
        assert events == ["start app", "start feature", "start router"]
    assert events == [
        "start app",
        "start feature",
        "start router",
        "stop router",
        "stop feature",
        "stop app",
    ]


def test_installation_after_startup_fails_explicitly() -> None:
    app = FastAPI()
    with TestClient(app):
        with pytest.raises(ConfigurationError, match="before the app starts"):
            Composition(features=[Feature(name="late")]).apply(app)
        with pytest.raises(ConfigurationError, match="before the app starts"):
            ErrorRegistry(errors=[], type_base="urn:test:problems").install(app)


def test_startup_validates_before_opening_feature_resources() -> None:
    started = []

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        started.append(True)
        yield

    router = CanonRouter()
    router.get("/one", operation_id="duplicate")(lambda: None)
    router.get("/two", operation_id="duplicate")(lambda: None)
    app = Composition(
        features=[Feature(name="invalid", routers=[router], lifespan=lifespan)]
    ).apply(FastAPI())
    with (
        pytest.warns(UserWarning, match="Duplicate Operation ID"),
        pytest.raises(
            ConfigurationError, match=r"duplicate operationId.*GET /one.*GET /two"
        ),
        TestClient(app),
    ):
        pytest.fail("invalid contracts must prevent startup")
    assert not started
    assert app.openapi_schema is None


@pytest.mark.parametrize(
    "accepted, denial", [(False, True), (False, False), (True, True)]
)
def test_domain_errors_respect_websocket_transport(
    accepted: bool, denial: bool
) -> None:
    missing = Error(Missing, status=404, code="item_missing", title="Item missing")
    router = CanonRouter(error_registry=ErrorRegistry(errors=[missing]))

    @router.websocket("/ws")
    async def socket(websocket: WebSocket) -> None:
        if accepted:
            await websocket.accept()
        raise Missing

    app = Composition(features=[Feature(name="socket", routers=[router])]).apply(
        FastAPI()
    )
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        messages.append(message)

    asyncio.run(
        app(
            {
                "type": "websocket",
                "path": "/ws",
                "query_string": b"",
                "headers": [],
                "scheme": "ws",
                "root_path": "",
                "server": ("test", 80),
                "client": ("test", 1),
                "extensions": {"websocket.http.response": {}} if denial else {},
            },
            receive,
            send,
        )
    )
    if denial and not accepted:
        assert messages[0]["type"] == "websocket.http.response.start"
        assert messages[0]["status"] == 404
        assert b"item_missing" in messages[1]["body"]
    else:
        assert messages[-1] == {
            "type": "websocket.close",
            "code": 1008,
            "reason": "item_missing",
        }


def test_stream_authentication_uses_the_same_http_problem_contract() -> None:
    router = CanonRouter(http_statuses=[401])

    @router.get("/events", response=CanonResponse.sse())
    async def events() -> StreamingResponse:
        raise HTTPException(401, "Sign in before opening the stream")

    app = Composition(features=[Feature(name="events", routers=[router])]).apply(
        FastAPI()
    )
    with TestClient(app) as client:
        response = client.get("/events")
    assert response.status_code == 401
    _validate_payload(app.openapi(), "/events", 401, response.json())
    assert set(
        app.openapi()["paths"]["/events"]["get"]["responses"]["200"]["content"]
    ) == {"text/event-stream"}


def test_failure_after_stream_start_does_not_send_a_second_response() -> None:
    missing = Error(Missing, status=404, code="item_missing", title="Item missing")
    router = CanonRouter(error_registry=ErrorRegistry(errors=[missing]))

    async def chunks() -> AsyncIterator[str]:
        yield "data: ready\n\n"
        raise Missing

    @router.get("/events", response=CanonResponse.sse(), raises=[missing])
    async def events() -> StreamingResponse:
        return StreamingResponse(chunks(), media_type="text/event-stream")

    app = Composition(features=[Feature(name="events", routers=[router])]).apply(
        FastAPI()
    )
    messages: list[Message] = []

    async def receive() -> Message:
        await asyncio.Event().wait()
        raise AssertionError("stream disconnect listener should be cancelled")

    async def send(message: Message) -> None:
        messages.append(message)

    with pytest.raises((RuntimeError, ExceptionGroup)) as failure:
        asyncio.run(
            app(
                {
                    "type": "http",
                    "asgi": {"spec_version": "2.4"},
                    "method": "GET",
                    "path": "/events",
                    "query_string": b"",
                    "headers": [],
                    "scheme": "http",
                    "root_path": "",
                    "http_version": "1.1",
                    "server": ("test", 80),
                    "client": ("test", 1),
                },
                receive,
                send,
            )
        )

    if isinstance(failure.value, ExceptionGroup):
        assert failure.value.subgroup(Missing) is not None
    else:
        assert "response already started" in str(failure.value)

    starts = [
        message for message in messages if message["type"] == "http.response.start"
    ]
    assert len(starts) == 1
    assert starts[0]["status"] == 200
    assert messages[1]["body"] == b"data: ready\n\n"
    assert b"application/problem+json" not in repr(messages).encode()


def _validate_payload(
    document: dict[str, Any], path: str, status: int, payload: Any
) -> None:
    schema = document["paths"][path]["get"]["responses"][str(status)]["content"][
        "application/problem+json"
    ]["schema"]
    Draft202012Validator({**schema, "components": document["components"]}).validate(
        payload
    )
