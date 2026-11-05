import asyncio
from collections.abc import AsyncIterator
from functools import partial

import pytest
from dishka import Provider, Scope, provide
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_canon import APIRouter, Composition, Feature
from tests.fixtures.injected_endpoints import AsyncEcho, EndpointStore, SyncEcho, reader


@pytest.mark.parametrize(
    ("kind", "prefix"),
    [
        ("async-object", "async"),
        ("sync-object", "sync"),
        ("bound-async", "bound"),
        ("bound-sync", "bound-sync"),
        ("inherited", "inherited"),
        ("partial-async", "partial-async"),
        ("partial-sync", "partial-sync"),
        ("keyword-async", "keyword-async"),
        ("keyword-sync", "keyword-sync"),
        ("bound-positional", "bound-positional"),
    ],
)
def test_callable_forms_resolve_both_markers_and_close_request_resources(
    kind: str, prefix: str
) -> None:
    opened: list[int] = []
    closed: list[int] = []

    class Services(Provider):
        @provide(scope=Scope.REQUEST)
        async def store(self) -> AsyncIterator[EndpointStore]:
            identifier = len(opened) + 1
            opened.append(identifier)
            try:
                yield EndpointStore(identifier)
            finally:
                closed.append(identifier)

    endpoint = reader(kind)
    attributes = dict(getattr(endpoint, "__dict__", {}))
    keywords = dict(endpoint.keywords) if isinstance(endpoint, partial) else None
    router = APIRouter(namespace="readers")
    router.get("/read")(endpoint)
    composition = Composition(
        features=[Feature(name="readers", routers=[router], providers=[Services()])]
    )
    app = FastAPI()
    report = composition.check(app)
    assert report.complete and not report.has_errors
    composition.build(app)
    assert opened == closed == []
    assert getattr(endpoint, "__dict__", {}) == attributes
    assert composition.inspect(app).operation(endpoint).path == "/read"
    operation = app.openapi()["paths"]["/read"]["get"]
    assert [item["name"] for item in operation.get("parameters", [])] == (
        ["prefix"] if kind.startswith("keyword") else []
    )
    assert "requestBody" not in operation

    async def scenario() -> None:
        async with (
            asyncio.timeout(5),
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app), base_url="http://test", timeout=5
            ) as client,
        ):
            assert (await client.get("/read")).json() == f"{prefix}:1:True"
            query = {"prefix": "override"} if kind.startswith("keyword") else None
            expected = "override" if query else prefix
            assert (
                await client.get("/read", params=query)
            ).json() == f"{expected}:2:True"
            assert closed == [1, 2]

    asyncio.run(scenario())
    assert opened == closed == [1, 2]
    assert getattr(endpoint, "__dict__", {}) == attributes
    if isinstance(endpoint, partial):
        assert endpoint.keywords == keywords


@pytest.mark.parametrize("factory", [AsyncEcho, SyncEcho])
def test_non_injected_callable_objects_keep_future_annotated_public_models(
    factory: type[AsyncEcho] | type[SyncEcho],
) -> None:
    endpoint = factory()
    attributes = dict(vars(endpoint))
    router = APIRouter(namespace="echo")
    router.post("/echo")(endpoint)
    composition = Composition(features=[Feature(name="echo", routers=[router])])
    app = composition.build(FastAPI())
    operation = app.openapi()["paths"]["/echo"]["post"]
    assert operation["operationId"] == f"echo.{factory.__name__}"
    assert operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/PublicPayload")
    assert composition.inspect(app).operation(endpoint).path == "/echo"

    async def scenario() -> None:
        async with (
            asyncio.timeout(5),
            AsyncClient(
                transport=ASGITransport(app), base_url="http://test", timeout=5
            ) as client,
        ):
            response = await client.post("/echo", json={"value": 7})
            assert response.status_code == 200
            assert response.json() == {"value": 7}

    asyncio.run(scenario())
    assert vars(endpoint) == attributes
