import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

import pytest
from dishka import Provider, Scope
from fastapi import Body, Depends, FastAPI, File, UploadFile
from httpx import ASGITransport, AsyncClient

from fastapi_canon import APIRouter, BinaryBody, Composition, Feature, Inject
from fastapi_canon.errors import ConfigurationError
from fastapi_canon.openapi import export_openapi

BinaryChunks = Annotated[AsyncIterator[bytes], BinaryBody("application/octet-stream")]


async def test_binary_body_streams_until_the_client_finishes_sending() -> None:
    router = APIRouter()
    first_received = asyncio.Event()

    @router.put("/files/{file_id}")
    async def upload(
        file_id: str, chunks: BinaryChunks, prefix: Inject[str]
    ) -> dict[str, str]:
        data = bytearray()
        async for chunk in chunks:
            data.extend(chunk)
            if data == b"\x00first":
                first_received.set()
        return {"id": prefix + file_id, "hex": data.hex()}

    async def send_chunks() -> AsyncIterator[bytes]:
        yield b"\x00first"
        await asyncio.wait_for(first_received.wait(), timeout=5)
        yield b"last\xff"

    provider = Provider()
    provider.provide(lambda: "stored-", provides=str, scope=Scope.APP)
    app = Composition(
        features=[Feature(name="uploads", routers=[router], providers=[provider])]
    ).build(FastAPI())

    operation = export_openapi(app)["paths"]["/files/{file_id}"]["put"]
    assert operation["requestBody"] == {
        "required": True,
        "content": {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }
    async with (
        asyncio.timeout(10),
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
    ):
        response = await client.put(
            "/files/report",
            content=send_chunks(),
            headers={"Content-Type": "application/octet-stream"},
        )
    assert response.status_code == 200
    assert response.json() == {"id": "stored-report", "hex": "0066697273746c617374ff"}


def test_binary_body_rejects_two_stream_consumers() -> None:
    router = APIRouter()

    async def upload(first: BinaryChunks, second: BinaryChunks) -> None:
        pass

    with pytest.raises(ConfigurationError, match="only one BinaryBody"):
        router.put("/files")(upload)


@pytest.mark.parametrize(
    "body_type",
    [Annotated[bytes, Body()], Annotated[UploadFile, File()]],
    ids=["buffered", "multipart"],
)
def test_binary_body_rejects_a_second_body_parameter(body_type: object) -> None:
    router = APIRouter()

    async def upload(chunks: BinaryChunks, parsed: body_type) -> None:  # type: ignore[valid-type]
        pass

    with pytest.raises(ConfigurationError, match="cannot be combined with"):
        router.put("/files")(upload)


def test_binary_body_rejects_a_body_dependency_inherited_on_inclusion() -> None:
    router = APIRouter()

    @router.put("/files")
    async def upload(chunks: BinaryChunks) -> None:
        pass

    def consumes_body(parsed: Annotated[bytes, Body()]) -> None:
        pass

    app = FastAPI(dependencies=[Depends(consumes_body)])
    app.include_router(router)
    with pytest.raises(ConfigurationError, match="cannot be combined with"):
        app.openapi()


def test_binary_body_rejects_a_handwritten_request_body() -> None:
    router = APIRouter()

    async def upload(chunks: BinaryChunks) -> None:
        pass

    with pytest.raises(ConfigurationError, match="conflicts with openapi_extra"):
        router.put("/files", openapi_extra={"requestBody": {}})(upload)
