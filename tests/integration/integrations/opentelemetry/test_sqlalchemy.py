import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from threading import Event
from typing import Any

import pytest
import sqlalchemy
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import Gauge, Histogram, InMemoryMetricReader
from opentelemetry.trace import StatusCode
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import QueuePool

from fastapi_canon import Composition, Database, Feature, FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry import Telemetry
from fastapi_canon.integrations.opentelemetry.sqlalchemy import (
    SqlAlchemyInstrumentation,
    instrument_sqlalchemy,
)
from tests.fixtures.telemetry import SpanCapture


@pytest.mark.parametrize("capture_statement", [False, True])
@pytest.mark.parametrize("declarative", [False, True])
def test_engine_local_queries_privacy_failure_and_cleanup(
    capture_statement: bool,
    declarative: bool,
) -> None:
    factory = sqlalchemy.create_engine
    global_provider = trace.get_tracer_provider()
    first = create_engine("sqlite://")
    other = create_engine("sqlite://")
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        with SpanCapture() as capture:
            options = Telemetry(tracer_provider=capture.provider, meter_provider=meter)
            manager = (
                SqlAlchemyInstrumentation(
                    telemetry=options, capture_statement=capture_statement
                )(first)
                if declarative
                else instrument_sqlalchemy(
                    first, options, capture_statement=capture_statement
                )
            )
            with manager as bound:
                assert bound is first and sqlalchemy.create_engine is factory
                with other.connect() as connection:
                    assert connection.scalar(text("select 2")) == 2
                assert not capture.spans
                with first.connect() as connection:
                    assert (
                        connection.scalar(text("select 'secret-literal'"))
                        == "secret-literal"
                    )
                    with pytest.raises(OperationalError):
                        connection.execute(text("select * from secret_missing_table"))
                with (
                    pytest.raises(FeatureConfigurationError, match="already"),
                    instrument_sqlalchemy(first, options),
                ):
                    pass
            with first.connect() as connection:
                assert connection.scalar(text("select 3")) == 3
            assert len(capture.spans) == 2
            success, failure = capture.spans
            assert ("secret-literal" in str(success.attributes)) is capture_statement
            assert failure.status.status_code is StatusCode.ERROR
            assert (
                failure.attributes is not None
                and failure.attributes["error.type"] == "OperationalError"
            )
            assert not failure.events
            if not capture_statement:
                assert "secret_missing_table" not in str(failure.attributes)
            assert not trace.get_current_span().get_span_context().is_valid
            with capture.provider.get_tracer("owner").start_as_current_span("owner"):
                pass
            assert capture.one("owner").name == "owner"
            assert _query_count(reader) == 2
    finally:
        first.dispose()
        other.dispose()
        meter.shutdown()
    assert trace.get_tracer_provider() is global_provider


def test_suppressed_queries_still_observe_pool_usage_and_detach_independently() -> None:
    first = create_engine("sqlite://", poolclass=QueuePool)
    second = create_engine("sqlite://", poolclass=QueuePool)
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        with SpanCapture() as capture:
            options = Telemetry(tracer_provider=capture.provider, meter_provider=meter)
            with instrument_sqlalchemy(second, options, pool_name="second"):
                with instrument_sqlalchemy(first, options, pool_name="first"):
                    with suppress_instrumentation(), first.connect() as connection:
                        assert connection.scalar(text("select 1")) == 1
                        assert not capture.spans
                        assert _query_count(reader) == 0
                        assert _pool_values(reader)[("first", "used")] == 1
                    values = _pool_values(reader)
                    assert values[("first", "used")] == 0
                    assert values[("first", "idle")] == 1
                with first.connect() as connection:
                    assert connection.scalar(text("select 2")) == 2
                with second.connect() as connection:
                    assert connection.scalar(text("select 3")) == 3
                assert len(capture.spans) == 1
                assert _query_count(reader) == 1
                assert ("first", "used") not in _pool_values(reader)
                assert _pool_values(reader)[("second", "idle")] == 1
            assert _pool_values(reader) == {}
    finally:
        first.dispose()
        second.dispose()
        meter.shutdown()


@pytest.mark.parametrize(
    ("tracing", "metrics"), [(True, False), (False, True), (False, False)]
)
async def test_async_engine_independent_signals_and_borrowed_lifetime(
    tracing: bool, metrics: bool
) -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        with SpanCapture() as capture:
            options = Telemetry(
                tracer_provider=capture.provider,
                meter_provider=meter,
                tracing=tracing,
                metrics=metrics,
            )
            async with asyncio.timeout(5):
                with SqlAlchemyInstrumentation(telemetry=options)(engine):
                    async with engine.connect() as connection:
                        assert await connection.scalar(text("select 1")) == 1
                        with pytest.raises(OperationalError):
                            await connection.execute(text("select * from absent_table"))
                    assert not trace.get_current_span().get_span_context().is_valid
                async with engine.connect() as connection:
                    assert await connection.scalar(text("select 2")) == 2
            assert len(capture.spans) == 2 * int(tracing)
            assert _query_count(reader) == 2 * int(metrics)
    finally:
        await engine.dispose()
        meter.shutdown()


def test_engine_adapter_preserves_foreign_listeners_and_rejects_duplicate_pool_names() -> (
    None
):
    first = create_engine("sqlite://")
    second = create_engine("sqlite://")
    calls: list[str] = []

    def listener(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        calls.append(statement)

    event.listen(first, "before_cursor_execute", listener)
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        with SpanCapture() as capture:
            options = Telemetry(tracer_provider=capture.provider, meter_provider=meter)
            with instrument_sqlalchemy(first, options):
                with (
                    pytest.raises(
                        FeatureConfigurationError, match="pool_name must be unique"
                    ),
                    instrument_sqlalchemy(second, options),
                ):
                    pass
                with first.connect() as connection:
                    assert connection.scalar(text("select 1")) == 1
            with first.connect() as connection:
                assert connection.scalar(text("select 2")) == 2
            assert len(capture.spans) == 1
            assert calls == ["select 1", "select 2"]
    finally:
        event.remove(first, "before_cursor_execute", listener)
        first.dispose()
        second.dispose()
        meter.shutdown()


async def test_async_query_cancellation_finishes_span_and_restores_context() -> None:
    started = Event()
    release = Event()
    engine = create_async_engine("sqlite+aiosqlite://")

    def blocking_query() -> int:
        started.set()
        assert release.wait(timeout=3)
        return 1

    def connected(connection: Any, record: Any) -> None:
        connection.create_function("blocking_query", 0, blocking_query)

    event.listen(engine.sync_engine, "connect", connected)
    try:
        with SpanCapture() as capture:
            options = Telemetry(tracer_provider=capture.provider, metrics=False)
            async with asyncio.timeout(5):
                with SqlAlchemyInstrumentation(telemetry=options)(engine):

                    async def query() -> None:
                        async with engine.connect() as connection:
                            await connection.execute(text("select blocking_query()"))

                    task = asyncio.create_task(query())
                    try:
                        assert await asyncio.to_thread(started.wait, 3)
                        task.cancel()
                        release.set()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    finally:
                        release.set()
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                    async with engine.connect() as connection:
                        assert await connection.scalar(text("select 2")) == 2
                    assert not trace.get_current_span().get_span_context().is_valid
            assert len(capture.spans) == 2
            assert capture.spans[0].status.status_code is StatusCode.UNSET
            assert not capture.spans[0].events
    finally:
        release.set()
        event.remove(engine.sync_engine, "connect", connected)
        await engine.dispose()


@pytest.mark.parametrize("pool_metrics", [False, True])
def test_declaration_reuses_configuration_with_fresh_engine_contexts(
    pool_metrics: bool,
) -> None:
    first = create_engine("sqlite://", poolclass=QueuePool)
    second = create_engine("sqlite://", poolclass=QueuePool)
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        with SpanCapture() as capture:
            instrumentation = SqlAlchemyInstrumentation(
                telemetry=Telemetry(
                    tracer_provider=capture.provider, meter_provider=meter
                ),
                pool_name="backend",
                pool_metrics=pool_metrics,
            )
            with instrumentation(first) as bound, first.connect() as connection:
                assert bound is first
                assert connection.scalar(text("select 1")) == 1
                assert _pool_values(reader) == (
                    {("backend", "used"): 1, ("backend", "idle"): 0}
                    if pool_metrics
                    else {}
                )
            assert _pool_values(reader) == {}
            with instrumentation(second) as bound, second.connect() as connection:
                assert bound is second
                assert connection.scalar(text("select 2")) == 2
            assert _pool_values(reader) == {}
            with instrumentation(first), first.connect() as connection:
                assert connection.scalar(text("select 3")) == 3
            with first.connect() as connection:
                assert connection.scalar(text("select 4")) == 4
            assert len(capture.spans) == 3
            assert _query_count(reader) == 3
            assert _pool_values(reader) == {}
    finally:
        first.dispose()
        second.dispose()
        meter.shutdown()


@pytest.mark.parametrize("fail_startup", [False, True])
async def test_database_declaration_detaches_before_disposal_and_after_feature_cleanup(
    fail_startup: bool,
) -> None:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    pool_at_disposal: list[dict[tuple[str, str], int]] = []

    def disposed(engine: object) -> None:
        pool_at_disposal.append(_pool_values(reader))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = await app.state.dishka_container.get(AsyncEngine)
        event.listen(engine.sync_engine, "engine_disposed", disposed)
        try:
            async with engine.connect() as connection:
                assert await connection.scalar(text("select 1")) == 1
            if fail_startup:
                raise RuntimeError("feature startup failed")
            yield
        finally:
            async with engine.connect() as connection:
                assert await connection.scalar(text("select 2")) == 2

    try:
        with SpanCapture() as capture:
            database = Database(
                url="sqlite://",
                engine_context=SqlAlchemyInstrumentation(
                    telemetry=Telemetry(
                        tracer_provider=capture.provider, meter_provider=meter
                    ),
                    pool_name="backend",
                    capture_statement=True,
                ),
            )
            app = Composition(
                features=[database.feature, Feature(name="consumer", lifespan=lifespan)]
            ).build(FastAPI())
            async with asyncio.timeout(10):
                with (
                    pytest.raises(RuntimeError, match="feature startup failed")
                    if fail_startup
                    else nullcontext()
                ):
                    async with app.router.lifespan_context(app):
                        assert _pool_values(reader) == {
                            ("backend", "used"): 0,
                            ("backend", "idle"): 1,
                        }
            statements = [
                span.attributes.get("db.query.text")
                for span in capture.spans
                if span.attributes is not None
            ]
            assert statements == ["BEGIN", "select 1", "BEGIN", "select 2"]
            assert pool_at_disposal == [{}]
            assert _pool_values(reader) == {}
            with capture.provider.get_tracer("owner").start_as_current_span("owner"):
                pass
            assert capture.one("owner").name == "owner"
    finally:
        meter.shutdown()


def _query_count(reader: InMemoryMetricReader) -> int:
    data = reader.get_metrics_data()
    if data is None:
        return 0
    return sum(
        point.count
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "db.client.operation.duration"
        and isinstance(metric.data, Histogram)
        for point in metric.data.data_points
    )


def _pool_values(reader: InMemoryMetricReader) -> dict[tuple[str, str], int]:
    data = reader.get_metrics_data()
    if data is None:
        return {}
    return {
        (
            str(point.attributes["db.client.connection.pool.name"]),
            str(point.attributes["db.client.connection.state"]),
        ): int(point.value)
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "db.client.connection.count"
        and isinstance(metric.data, Gauge)
        for point in metric.data.data_points
        if point.attributes is not None
    }
