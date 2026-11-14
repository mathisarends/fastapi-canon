"""Engine-local SQL query telemetry and independent pool observations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary, WeakSet, ref

from fastapi_canon.exceptions import FeatureConfigurationError
from fastapi_canon.integrations.opentelemetry.declarations import Telemetry
from fastapi_canon.integrations.opentelemetry.resources import claim_resource

if TYPE_CHECKING:
    from opentelemetry.metrics import CallbackOptions, Meter, Observation
    from sqlalchemy import Engine


@contextmanager
def instrument_sqlalchemy[T](
    engine: T,
    telemetry: Telemetry,
    *,
    pool_name: str = "default",
    pool_metrics: bool = True,
    capture_statement: bool = False,
) -> Iterator[T]:
    """Attach only engine-local listeners and remove only those listeners on exit.

    The resource owner must finish operations before leaving the scope. Neither
    the engine nor borrowed SDK providers are closed. Query text is omitted unless
    explicitly enabled; parameters and exception messages are never recorded.
    Pool observations are independent of request suppression.
    """
    if not telemetry.tracing and not telemetry.metrics:
        yield engine
        return
    from sqlalchemy import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine

    target = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    if not isinstance(target, Engine):
        raise FeatureConfigurationError(
            "instrument_sqlalchemy requires an Engine or AsyncEngine"
        )
    if not pool_name:
        raise FeatureConfigurationError("pool_name must not be empty")
    with claim_resource(target):
        observer = _EngineInstrumentation(
            target, telemetry, pool_name, pool_metrics, capture_statement
        )
        try:
            observer.install()
            yield engine
        finally:
            observer.remove()


@dataclass(frozen=True, slots=True, kw_only=True)
class SqlAlchemyInstrumentation:
    """Reusable engine-context configuration for SQLAlchemy telemetry.

    Pass this declaration to ``Database(engine_context=...)``. Each engine gets
    a fresh instrumentation context; engines and SDK providers stay borrowed.
    """

    telemetry: Telemetry
    pool_name: str = "default"
    pool_metrics: bool = True
    capture_statement: bool = False

    def __call__[T](self, engine: T) -> AbstractContextManager[T]:
        return instrument_sqlalchemy(
            engine,
            self.telemetry,
            pool_name=self.pool_name,
            pool_metrics=self.pool_metrics,
            capture_statement=self.capture_statement,
        )


class _EngineInstrumentation:
    def __init__(
        self,
        engine: Engine,
        telemetry: Telemetry,
        pool_name: str,
        pool_metrics: bool,
        capture_statement: bool,
    ) -> None:
        from opentelemetry import metrics, trace

        self._engine = engine
        self._tracer = (
            trace.get_tracer(
                "fastapi_canon.sqlalchemy", tracer_provider=telemetry.tracer_provider
            )
            if telemetry.tracing
            else None
        )
        meter = (
            metrics.get_meter(
                "fastapi_canon.sqlalchemy", meter_provider=telemetry.meter_provider
            )
            if telemetry.metrics
            else None
        )
        self._duration = (
            meter.create_histogram("db.client.operation.duration", unit="s")
            if meter is not None
            else None
        )
        self._attributes = {"db.system.name": engine.dialect.name}
        self._capture_statement = capture_statement
        self._pending: dict[int, AbstractContextManager[None]] = {}
        self._listeners: list[tuple[str, Any]] = []
        self._lock = Lock()
        self._pool_state = _PoolState(engine, pool_name)
        self._pool_metrics = meter is not None and pool_metrics
        if self._pool_metrics and meter is not None:
            _pool_observations(meter).add(self._pool_state)

    def install(self) -> None:
        from sqlalchemy import event

        listeners: list[tuple[str, Callable[..., Any]]] = [
            ("before_cursor_execute", self._before),
            ("after_cursor_execute", self._after),
            ("handle_error", self._error),
        ]
        if self._pool_metrics:
            listeners.extend(
                [
                    ("connect", self._pool_state.connect),
                    ("checkout", self._pool_state.checkout),
                    ("checkin", self._pool_state.checkin),
                    ("close", self._pool_state.close),
                ]
            )
        for name, callback in listeners:
            event.listen(self._engine, name, callback)
            self._listeners.append((name, callback))

    def remove(self) -> None:
        from sqlalchemy import event

        self._pool_state.active = False
        for name, callback in reversed(self._listeners):
            event.remove(self._engine, name, callback)
        self._listeners.clear()

    def _before(
        self,
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        from opentelemetry.instrumentation.utils import is_instrumentation_enabled

        if not is_instrumentation_enabled():
            return
        manager = self._query(statement)
        manager.__enter__()
        with self._lock:
            self._pending[id(context)] = manager

    def _after(
        self,
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        with self._lock:
            manager = self._pending.pop(id(context), None)
        if manager is not None:
            manager.__exit__(None, None, None)

    def _error(self, context: Any) -> None:
        with self._lock:
            manager = self._pending.pop(id(context.execution_context), None)
        if manager is not None:
            error = context.original_exception
            manager.__exit__(type(error), error, error.__traceback__)

    @contextmanager
    def _query(self, statement: str) -> Iterator[None]:
        from opentelemetry.trace import SpanKind, StatusCode

        attributes = dict(self._attributes)
        if self._capture_statement:
            attributes["db.query.text"] = statement
        outcome = dict(self._attributes)
        started = perf_counter()
        with (
            self._tracer.start_as_current_span(
                "DB query",
                kind=SpanKind.CLIENT,
                attributes=attributes,
                record_exception=False,
                set_status_on_exception=False,
            )
            if self._tracer is not None
            else nullcontext(None)
        ) as span:
            try:
                yield
            except Exception as error:
                outcome["error.type"] = type(error).__qualname__
                if span is not None:
                    span.set_attribute("error.type", outcome["error.type"])
                    span.set_status(StatusCode.ERROR)
                raise
            finally:
                if self._duration is not None:
                    self._duration.record(perf_counter() - started, outcome)


class _PoolState:
    def __init__(self, engine: Engine, name: str) -> None:
        self.active = True
        self._engine = ref(engine)
        self._attributes = {
            "db.system.name": engine.dialect.name,
            "db.client.connection.pool.name": name,
        }
        self._connections: set[int] = set()
        self._used: set[int] = set()
        self._lock = Lock()

    def connect(self, connection: Any, record: Any) -> None:
        with self._lock:
            self._connections.add(id(record))

    def checkout(self, connection: Any, record: Any, proxy: Any) -> None:
        with self._lock:
            self._connections.add(id(record))
            self._used.add(id(record))

    def checkin(self, connection: Any, record: Any) -> None:
        with self._lock:
            self._used.discard(id(record))

    def close(self, connection: Any, record: Any) -> None:
        with self._lock:
            self._connections.discard(id(record))
            self._used.discard(id(record))

    @property
    def identity(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._attributes.items()))

    def observe(self, options: CallbackOptions) -> Iterator[Observation]:
        from opentelemetry.metrics import Observation

        engine = self._engine()
        if not self.active or engine is None:
            return
        checked_out = getattr(engine.pool, "checkedout", None)
        checked_in = getattr(engine.pool, "checkedin", None)
        with self._lock:
            used = checked_out() if callable(checked_out) else len(self._used)
            idle = (
                checked_in()
                if callable(checked_in)
                else len(self._connections - self._used)
            )
        yield Observation(
            used, {**self._attributes, "db.client.connection.state": "used"}
        )
        yield Observation(
            idle, {**self._attributes, "db.client.connection.state": "idle"}
        )


class _PoolObservations:
    def __init__(self) -> None:
        self._states: WeakSet[_PoolState] = WeakSet()
        self._lock = Lock()

    def add(self, state: _PoolState) -> None:
        with self._lock:
            if any(
                existing.active and existing.identity == state.identity
                for existing in self._states
            ):
                raise FeatureConfigurationError(
                    "pool_name must be unique for engines sharing a meter"
                )
            self._states.add(state)

    def observe(self, options: CallbackOptions) -> Iterator[Observation]:
        with self._lock:
            states = tuple(self._states)
        for state in states:
            yield from state.observe(options)


_POOL_OBSERVATIONS: WeakKeyDictionary[Meter, _PoolObservations] = WeakKeyDictionary()
_POOL_LOCK = Lock()


def _pool_observations(meter: Meter) -> _PoolObservations:
    with _POOL_LOCK:
        observer = _POOL_OBSERVATIONS.get(meter)
        if observer is None:
            observer = _PoolObservations()
            meter.create_observable_gauge(
                "db.client.connection.count",
                unit="{connection}",
                callbacks=[observer.observe],
            )
            _POOL_OBSERVATIONS[meter] = observer
        return observer
