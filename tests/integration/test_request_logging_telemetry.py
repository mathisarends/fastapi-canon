import asyncio
import logging

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.trace import SpanKind

from fastapi_canon import Composition, Feature
from fastapi_canon.error import ErrorRegistry
from fastapi_canon.integrations.opentelemetry import Telemetry
from tests.fixtures.telemetry import SpanCapture


@pytest.mark.parametrize("problem_details", [True, False])
async def test_overlapping_requests_correlate_with_exported_server_spans(
    caplog: pytest.LogCaptureFixture, problem_details: bool
) -> None:
    caplog.set_level(logging.INFO, logger="fastapi_canon.access")
    entered = asyncio.Event()
    overlap = asyncio.Event()
    app = FastAPI()

    @app.get("/tasks/{task_id}")
    async def task(task_id: int) -> int:
        if task_id == 1:
            entered.set()
            await overlap.wait()
            return task_id
        await entered.wait()
        overlap.set()
        raise RuntimeError("unexpected")

    with SpanCapture() as capture:
        Composition(
            features=(
                Feature(
                    name="tasks",
                    errors=ErrorRegistry(
                        errors=[], type_base="https://example.test/problems"
                    ),
                ),
            ),
            request_logging=True,
            telemetry=Telemetry(
                tracer_provider=capture.provider,
                metrics=False,
                logs=False,
                problem_details=problem_details,
            ),
        ).build(app)
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client,
            asyncio.timeout(3),
        ):
            responses = await asyncio.gather(
                client.get(
                    "/tasks/1",
                    headers={"traceparent": "00-" + "1" * 32 + "-" + "a" * 16 + "-01"},
                ),
                client.get(
                    "/tasks/2",
                    headers={"traceparent": "00-" + "2" * 32 + "-" + "b" * 16 + "-01"},
                ),
            )
        assert [r.status_code for r in responses] == [200, 500]
        records = [r for r in caplog.records if r.name == "fastapi_canon.access"]
        servers = [s for s in capture.spans if s.kind is SpanKind.SERVER]
        assert len(records) == len(servers) == 2
        by_status = {r.status: r for r in records}
        assert by_status[200].trace_id == "1" * 32
        assert by_status[500].trace_id == "2" * 32
        assert by_status[500].problem_code == "internal_server_error"
        for span in servers:
            assert span.context is not None and span.attributes is not None
            record = by_status[span.attributes["http.response.status_code"]]
            assert record.trace_id == f"{span.context.trace_id:032x}"
            assert record.span_id == f"{span.context.span_id:016x}"
            assert record.route == "/tasks/{task_id}"
            assert record.event == "http.request.completed"
            assert record.exc_info is None
