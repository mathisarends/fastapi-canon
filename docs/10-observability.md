# 10. Observability

Trace your services with OpenTelemetry and log one record per request, without
touching handlers or domain code. Install `fastapi-canon[otel]`.

## Tracing

```python
from fastapi_canon.integrations.opentelemetry import Telemetry, console, instrument

tasks = Feature(
    name="tasks",
    routers=[router],
    providers=[TaskProvider],
    integrations=[
        instrument(TaskRepository),
        instrument(TaskService, expected=(TaskNotFound,)),
    ],
)
app = Composition(
    features=[tasks],
    integrations=[Telemetry(sdk=console(service_name="tasks"))],
).build(FastAPI())
```

Every public `async` method of an instrumented port gets a span, nested under
FastAPI's HTTP span. Declared errors below 500 and `expected=` exceptions are not
recorded as failures. `console(...)` prints spans as JSON; for a real exporter,
pass your own SDK factory:

```python
@contextmanager
def otlp() -> Iterator[SdkProviders]:
    provider = TracerProvider(resource=Resource.create({"service.name": "tasks"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    try:
        yield SdkProviders(tracer_provider=provider)
    finally:
        provider.shutdown()


Telemetry(sdk=otlp)
```

## Useful options

| Option | Use |
| --- | --- |
| `exclude=lambda scope: scope["path"] in {"/health"}` | Skip health checks |
| `port_metrics=True` | Record port durations as metrics |
| `Telemetry.disabled()` | Keep `instrument(...)` declarations, record nothing |

HTTPX clients and SQLAlchemy engines from your providers are instrumented
automatically with `fastapi-canon[otel-httpx]` or `[otel-sqlalchemy]`.

## Request logging

```python
app = Composition(features=features, request_logging=True).build(FastAPI())
```

Each response produces one `http.request.completed` record on the
`fastapi_canon.access` logger:

```json
{"method": "POST", "route": "/tasks/{task_id}", "status": 404, "duration_ms": 8.4, "problem_code": "task_not_found"}
```

With tracing enabled, records also carry `trace_id` and `span_id`. URLs, headers
and bodies are never logged. Configure the logger's handler and level yourself.

WebSocket tracing is covered in [WebSocket observability](integrations/websockets/09-observability.md).

---

Previous: [9. Database](09-database.md) · [Learning path](README.md) · Next: [11. WebRTC](11-webrtc.md)
