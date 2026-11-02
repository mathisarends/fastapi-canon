# HTTP request logging

Enable one structured Python log record per HTTP response:

```python
import logging

from fastapi import FastAPI
from fastapi_canon import Composition, ErrorOptions

app = Composition(
    *features,
    errors=ErrorOptions(type_base="https://api.example.test/problems"),
    request_logging=True,
    telemetry=telemetry,  # Optional; omit for request logging alone.
).apply(FastAPI())
```

`request_logging` defaults to `False`. It needs no Canon OTel extra, SDK, exporter,
public logger class or additional configuration object. Install the composition
before serving requests. Reapplying the same composition does not add another
logger. Configure the `fastapi_canon.access` logger at `INFO` in application
startup; Canon does not configure handlers, formatters, levels or exporters.
Services continue to use `logging.getLogger(__name__)`.

The message and `event` attribute are `http.request.completed`. Each field is a
separate `LogRecord` attribute, ready for the application's existing JSON
formatter:

```json
{
  "event": "http.request.completed",
  "method": "POST",
  "route": "/api/v1/tasks/{task_id}/completion",
  "status": 404,
  "duration_ms": 8.4,
  "problem_code": "task_not_found"
}
```

`route` is the matched route template, or `None` for an unmatched router 404.
`status` is the successfully sent status, or `None` before response start.
`problem_code` is supplied directly by Canon's problem handlers and is absent on
other responses. Canon never buffers or parses a response body for logging, and
does not record raw URLs, query strings, headers, bodies or exception details.

`duration_ms` uses a monotonic clock from request entry through the last
successfully sent body chunk, including streaming and send backpressure. The
record is emitted immediately at that point, before background tasks. An
incomplete response, disconnect or cancellation produces exactly one
`http.request.aborted` instead. Its status remains the one already sent, if any.
A completed 500 response is a completion record, even if the exception subsequently
propagates to the ASGI server. Background-task failures after a complete response
do not create a second request record. WebSockets and lifespan events are ignored.

## Trace correlation and diagnostics

When an active span has a valid context at request entry, `trace_id` (32 lowercase
hex characters) and `span_id` (16 characters) identify that context. FastAPI's
native telemetry activates the HTTP server span outside this logger, so the
record identifies the server span, including for 500 responses. IDs are captured
per request before nested service spans run; overlapping requests remain isolated.
Both fields are absent without a valid context.

For ordinary Python service logs, configure the application's OpenTelemetry
logging handler at startup. It bridges Python logs and their current trace
context to the application's `LoggerProvider`. Optional formatter correlation
can use `opentelemetry-instrumentation-logging`'s trace context injection. Canon
does not install a process-wide LogRecord factory or log exporter.
`Telemetry.logs` continues to control FastAPI's native telemetry log events and
is independent of `request_logging`.

The access logger never adds a stack trace. Canon logs exceptions it consumes
internally (response validation and failed problem rendering) once on
`fastapi_canon.error.handlers`. Unhandled exceptions are normalized to a safe
problem response and re-raised by Starlette; Uvicorn owns their diagnostic.
For one traceback per propagated exception, retain `uvicorn.error`, use
`Telemetry(logs=False, ...)` to disable the additional native exception log, and
route Uvicorn's error logger to one application handler without duplicate
propagation. Turn off Uvicorn's separate access logger:

```console
uvicorn app.main:app --no-access-log
```

The [reference service](../examples/reference-service/README.md#local-telemetry-demo)
implements this startup configuration and provides Loki-to-Tempo and
Tempo-to-Loki links for a distributed tasks-to-notifier request.
