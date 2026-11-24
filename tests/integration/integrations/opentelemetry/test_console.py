import json

import httpx
import pytest
from fastapi import FastAPI

from fastapi_canon import APIRouter, Composition, Feature
from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry import Telemetry, console


async def test_console_prints_spans_under_the_service_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    router = APIRouter()

    @router.get("/orders")
    async def get_orders() -> str:
        return "ok"

    app = Composition(
        features=[Feature(name="orders", routers=[router])],
        integrations=[Telemetry(sdk=console(service_name="orders"))],
    ).build(FastAPI())

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:
            assert (await client.get("/orders")).status_code == 200

    output = capsys.readouterr().out
    decoder = json.JSONDecoder()
    spans = []
    index = 0
    while (start := output.find("{", index)) != -1:
        span, index = decoder.raw_decode(output, start)
        spans.append(span)
    server = next(span for span in spans if span["name"] == "GET /orders")
    assert server["resource"]["attributes"]["service.name"] == "orders"


@pytest.mark.parametrize("service_name", ["", "  ", None])
def test_console_requires_a_service_name(service_name: object) -> None:
    with pytest.raises(FeatureConfigurationError, match="service_name"):
        console(service_name=service_name)  # type: ignore[arg-type]  # Invalid on purpose.
