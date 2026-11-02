import asyncio
import logging
import subprocess
import sys
import textwrap
from collections.abc import AsyncIterator

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.background import BackgroundTask
from starlette.responses import Response
from starlette.types import Message, Scope

from fastapi_canon import CanonRouter, Composition, Feature, FeatureConfigurationError
from fastapi_canon.error import Error, ErrorRegistry


class TaskNotFound(Exception):
    pass


def application(*, enabled: bool = True) -> FastAPI:
    app = FastAPI()

    @app.get("/tasks/{task_id}")
    async def task(task_id: int) -> dict[str, int]:
        if task_id == 0:
            raise TaskNotFound
        if task_id == -1:
            raise RuntimeError("private failure")
        return {"id": task_id}

    ErrorRegistry(
        type_base="https://example.test/problems",
        errors=[
            Error(TaskNotFound, status=404, code="task_not_found", title="Missing")
        ],
    ).install(app)
    return Composition(request_logging=enabled).apply(app)


@pytest.mark.parametrize(
    ("path", "status", "route", "code"),
    [
        ("/tasks/1?secret=hidden", 200, "/tasks/{task_id}", None),
        ("/tasks/0", 404, "/tasks/{task_id}", "task_not_found"),
        ("/absent?secret=hidden", 404, None, "http_404"),
        ("/tasks/invalid", 422, "/tasks/{task_id}", "request_validation_error"),
        ("/tasks/-1", 500, "/tasks/{task_id}", "internal_server_error"),
    ],
)
def test_completion_fields(
    caplog: pytest.LogCaptureFixture,
    path: str,
    status: int,
    route: str | None,
    code: str | None,
) -> None:
    caplog.set_level(logging.INFO, logger="fastapi_canon.access")
    with TestClient(application(), raise_server_exceptions=False) as client:
        response = client.get(path, headers={"Authorization": "hidden"})
    assert response.status_code == status
    records = [r for r in caplog.records if r.name == "fastapi_canon.access"]
    assert len(records) == 1
    record = records[0]
    assert record.event == "http.request.completed"
    assert record.method == "GET"
    assert record.route == route
    assert record.status == status
    assert record.duration_ms >= 0
    assert record.exc_info is None
    assert getattr(record, "problem_code", None) == code
    assert hasattr(record, "problem_code") is (code is not None)
    assert not hasattr(record, "trace_id") and not hasattr(record, "span_id")
    assert "hidden" not in str(vars(record))
    assert not any(r.exc_info for r in caplog.records)


def test_disabled_by_default(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="fastapi_canon.access")
    with TestClient(Composition().apply(FastAPI())) as client:
        assert client.get("/absent").status_code == 404
    assert not [r for r in caplog.records if r.name == "fastapi_canon.access"]


def test_included_router_logs_complete_template_without_telemetry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="fastapi_canon.access")
    router = CanonRouter()

    @router.get("/tasks/{task_id:int}")
    async def task(task_id: int) -> int:
        return task_id

    app = Composition(
        features=(Feature(name="tasks", routers=[router]),),
        router_factory=lambda: CanonRouter(prefix="/api/v1"),
        request_logging=True,
    ).apply(FastAPI())
    with TestClient(app) as client:
        assert client.get("/api/v1/tasks/17").json() == 17
    records = [r for r in caplog.records if r.name == "fastapi_canon.access"]
    assert len(records) == 1
    assert records[0].route == "/api/v1/tasks/{task_id}"


def test_stream_duration_stops_before_background_tasks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="fastapi_canon.access")
    app = FastAPI()
    background_records: list[logging.LogRecord] = []

    async def chunks() -> AsyncIterator[bytes]:
        yield b"first"
        await asyncio.sleep(0.02)
        yield b"last"

    async def background() -> None:
        background_records.extend(
            r for r in caplog.records if r.name == "fastapi_canon.access"
        )
        await asyncio.sleep(0.02)

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(chunks(), background=BackgroundTask(background))

    ErrorRegistry(errors=[], type_base="https://example.test/problems").install(app)
    Composition(request_logging=True).apply(app)
    with TestClient(app) as client:
        assert client.get("/stream").content == b"firstlast"
    assert len(background_records) == 1
    record = background_records[0]
    assert record.event == "http.request.completed" and record.status == 200
    assert record.duration_ms >= 20
    assert len([r for r in caplog.records if r.name == record.name]) == 1


def test_stream_failure_after_headers_is_aborted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="fastapi_canon.access")
    app = FastAPI()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"first"
        raise RuntimeError("stream failed")

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(chunks())

    ErrorRegistry(errors=[], type_base="https://example.test/problems").install(app)
    Composition(request_logging=True).apply(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/stream").status_code == 200
    records = [r for r in caplog.records if r.name == "fastapi_canon.access"]
    assert len(records) == 1
    assert records[0].event == "http.request.aborted"
    assert records[0].status == 200 and records[0].route == "/stream"
    assert records[0].exc_info is None
    assert not hasattr(records[0], "problem_code")


@pytest.mark.parametrize("fail_on", ["http.response.start", "http.response.body"])
async def test_send_failure_records_only_successfully_sent_status(
    caplog: pytest.LogCaptureFixture, fail_on: str
) -> None:
    caplog.set_level(logging.INFO, logger="fastapi_canon.access")
    app = FastAPI()

    @app.get("/")
    async def index() -> Response:
        return Response(b"ok", status_code=201)

    Composition(request_logging=True).apply(app)
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        if message["type"] == fail_on:
            raise OSError("connection lost")

    async with asyncio.timeout(3):
        with pytest.raises(OSError, match="connection lost"):
            await app(scope, receive, send)
    records = [r for r in caplog.records if r.name == "fastapi_canon.access"]
    assert len(records) == 1
    assert records[0].event == "http.request.aborted"
    assert records[0].status == (201 if fail_on == "http.response.body" else None)


def test_composition_logging_identity_and_late_installation() -> None:
    composition = Composition(request_logging=True)
    app = composition.apply(FastAPI())
    assert composition.apply(app) is app
    composition.validate(app)
    with pytest.raises(FeatureConfigurationError, match="different composition"):
        Composition().apply(app)
    with TestClient(FastAPI()) as client:
        assert client.get("/").status_code == 404
        with pytest.raises(FeatureConfigurationError, match="before the app starts"):
            Composition(request_logging=True).apply(client.app)


@pytest.mark.parametrize("failure", ["renderer", "response_validation"])
def test_consumed_exceptions_retain_one_diagnostic(
    caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    caplog.set_level(logging.INFO)
    app = FastAPI()

    def broken_detail(exception: TaskNotFound) -> str:
        raise RuntimeError("renderer diagnosis")

    @app.get("/broken", response_model=int)
    async def broken() -> object:
        if failure == "renderer":
            raise TaskNotFound
        return "invalid response"

    ErrorRegistry(
        type_base="https://example.test/problems",
        errors=[
            Error(
                TaskNotFound,
                status=404,
                code="missing",
                title="Missing",
                detail=broken_detail,
            )
        ],
    ).install(app)
    Composition(request_logging=True).apply(app)
    with TestClient(app) as client:
        response = client.get("/broken")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_server_error"
    diagnostics = [r for r in caplog.records if r.exc_info]
    assert len(diagnostics) == 1
    assert diagnostics[0].name == "fastapi_canon.error.handlers"
    records = [r for r in caplog.records if r.name == "fastapi_canon.access"]
    assert len(records) == 1
    assert records[0].status == 500
    assert records[0].problem_code == "internal_server_error"
    assert records[0].exc_info is None


async def test_uvicorn_owns_one_propagated_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import socket

    import httpx

    caplog.set_level(logging.INFO)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                application(),
                log_config=None,
                access_log=False,
                lifespan="off",
                ws="none",
            )
        )
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(0.01)
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"http://127.0.0.1:{port}/tasks/-1")
                assert response.status_code == 500
        finally:
            server.should_exit = True
            async with asyncio.timeout(5):
                await serving
    diagnostics = [r for r in caplog.records if r.exc_info]
    assert len(diagnostics) == 1
    assert diagnostics[0].name == "uvicorn.error"
    assert diagnostics[0].exc_info is not None
    assert isinstance(diagnostics[0].exc_info[1], RuntimeError)
    records = [r for r in caplog.records if r.name == "fastapi_canon.access"]
    assert len(records) == 1 and records[0].status == 500
    assert records[0].event == "http.request.completed"


def test_request_logging_without_optional_telemetry_runtimes() -> None:
    script = textwrap.dedent("""
        import importlib.abc
        import sys

        class NoTelemetry(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                blocked = (
                    'opentelemetry.sdk', 'opentelemetry.instrumentation',
                    'opentelemetry.exporter', 'fastapi_canon.integrations.opentelemetry',
                )
                if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
                    raise ModuleNotFoundError(fullname)

        sys.meta_path.insert(0, NoTelemetry())
        # Recent FastAPI requires the API, but Canon needs none of the optional
        # instrumentation, SDK or exporter packages to log a request.
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import logging
        from fastapi_canon import Composition
        records = []
        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)
        logger = logging.getLogger('fastapi_canon.access')
        logger.setLevel(logging.INFO)
        logger.addHandler(Capture())
        app = Composition(request_logging=True).apply(FastAPI())
        with TestClient(app) as client:
            assert client.get('/missing').status_code == 404
        assert len(records) == 1
        assert records[0].event == 'http.request.completed'
        assert not hasattr(records[0], 'trace_id')
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
