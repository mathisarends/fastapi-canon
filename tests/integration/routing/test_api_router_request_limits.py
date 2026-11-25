import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

import pytest
from fastapi import FastAPI, Form
from httpx import ASGITransport, AsyncClient

from fastapi_canon import APIRouter, BinaryBody, Composition, Feature


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


def _upload_app(
    *,
    router_options: dict[str, Any] | None = None,
    route_options: dict[str, Any] | None = None,
    include_options: dict[str, Any] | None = None,
) -> FastAPI:
    files = APIRouter(**(router_options or {}))

    @files.put("/files", http_statuses=[413], **(route_options or {}))
    async def upload(
        chunks: Annotated[AsyncIterator[bytes], BinaryBody("application/octet-stream")],
    ) -> int:
        return sum([len(chunk) async for chunk in chunks])

    root = APIRouter()
    root.include_router(files, **(include_options or {}))
    return Composition(features=(Feature(name="files", routers=[root]),)).build(
        FastAPI()
    )


@pytest.mark.parametrize(
    "declaration",
    ["router_options", "route_options", "include_options"],
)
async def test_body_over_the_limit_matches_the_canon_problem_contract(
    declaration: str,
) -> None:
    app = _upload_app(**{declaration: {"max_body_size": 8}})
    async with (
        asyncio.timeout(5),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
    ):
        accepted = await client.put("/files", content=_chunks(b"x" * 8))
        streamed = await client.put("/files", content=_chunks(b"x" * 8, b"x"))
        fixed = await client.put("/files", content=b"x" * 9)

    declared = app.openapi()["paths"]["/files"]["put"]["responses"]["413"]["content"]
    assert set(declared) == {"application/problem+json"}
    assert accepted.json() == 8
    assert streamed.status_code == fixed.status_code == 413
    assert streamed.headers["content-type"] == fixed.headers["content-type"]
    assert fixed.headers["content-type"] == "application/problem+json"
    assert streamed.json() == fixed.json()
    assert fixed.json()["code"] == "http_413"


async def test_form_limits_reject_extra_fields_with_a_canon_problem() -> None:
    router = APIRouter()

    @router.post("/notes", form_limits={"max_fields": 1})
    async def note(title: Annotated[str, Form()]) -> str:
        return title

    app = Composition(features=(Feature(name="notes", routers=[router]),)).build(
        FastAPI()
    )
    async with (
        asyncio.timeout(5),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
    ):
        accepted = await client.post("/notes", data={"title": "a"})
        rejected = await client.post("/notes", data={"title": "a", "extra": "b"})

    assert accepted.json() == "a"
    assert rejected.status_code == 400
    assert rejected.headers["content-type"] == "application/problem+json"
    assert rejected.json()["code"] == "http_400"
