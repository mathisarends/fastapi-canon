"""Application startup owns the demo's single Python logging export path."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider


@contextmanager
def export_python_logs(provider: LoggerProvider) -> Iterator[None]:
    root = logging.getLogger()
    previous_level = root.level
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Uvicorn's own configuration installs a console handler on this parent.
    # Route its diagnostics through the same OTLP handler exactly once.
    uvicorn = logging.getLogger("uvicorn")
    previous_handlers = uvicorn.handlers[:]
    previous_propagate = uvicorn.propagate
    uvicorn.handlers.clear()
    uvicorn.propagate = True
    try:
        yield
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
        uvicorn.handlers[:] = previous_handlers
        uvicorn.propagate = previous_propagate
        handler.close()
