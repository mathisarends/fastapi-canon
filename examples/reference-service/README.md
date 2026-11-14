# Reference service

A small task API demonstrating feature slices, repositories, mapping, transactions,
and authentication with the real `fastapi-canon` library. Features contribute
providers, `APIRouter` declarations, and typed error registries. `Composition`
owns the Dishka container and its shutdown; the application has no local Feature
implementation or second container/lifespan manager.

The example defaults to SQLite for convenient local use. Persistent schemas should use Alembic; `app.schema` exists only to bootstrap this disposable example. Use Alembic migrations for persistent deployments.

From the repository root, install the shared environment, then run the example
from its own directory while explicitly selecting the root project:

```console
uv sync --frozen --all-groups
cd examples/reference-service
uv run --frozen --project ../.. python -m app.schema
uv run --frozen --project ../.. uvicorn app.main:app --reload
```

The task API is available under `/api/v1/tasks`; interactive documentation is at `/docs`. Task routes require `Authorization: Bearer local-development-token`. This token is a local example default only; deployed services inject real authentication configuration and a production `TokenVerifier` adapter.

From the repository root, use
`uv run --frozen --directory examples/reference-service --project ../.. python -m pytest -q`
for the HTTP/authentication/contract regressions. The example uses the root
`uv.lock`; it has no separate locked development environment.

Errors now use Canon's RFC 9457 `application/problem+json` contracts. The stable
codes are `authentication_required`, `task_not_found`, and
`request_validation_error`; `WWW-Authenticate: Bearer` is preserved for 401.
Successful response schemas and paths remain unchanged. Router `namespace=`
values name the operations `tasks.create`, `tasks.list_recent`,
`tasks.complete` and `system.health`, so generated SDKs group them as
`client.tasks.create(...)` without explicit operation IDs. The
versioned feature routes and the public `/health` endpoint are wired explicitly
at the composition root.

## Local telemetry demo

The normal application remains exporter-free. Its tasks feature declares
`instrument(TaskRepository)` and `instrument(TaskService, expected=(TaskNotFound,))`
in `Feature.instrumentation`. Composition adds their Dishka decorators only when
telemetry is enabled; TaskProvider remains usable in direct Dishka containers.
`create_app(telemetry=Telemetry(...))` accepts application-owned
providers. See the [developer API](../../docs/telemetry.md).

For the opt-in demo, run from the repository root:

```console
uv sync --frozen --all-groups
docker compose -f examples/reference-service/compose.telemetry.yml up -d
uv run --frozen --directory examples/reference-service --project ../.. python -m app.schema
```

Start these commands in two terminals:

```console
uv run --frozen --directory examples/reference-service --project ../.. uvicorn app.telemetry_demo:create_tasks_app --factory --port 8000 --no-access-log
uv run --frozen --directory examples/reference-service --project ../.. uvicorn app.telemetry_demo:create_notifier_app --factory --port 8001 --no-access-log
```

Send one request (PowerShell users can use `curl.exe`):

```console
curl -X POST http://localhost:8000/api/v1/demo/notify
```

This returns `{"status":"sent"}` and produces a trace containing both HTTP server
spans, a NotificationGateway port span and an outgoing HTTPX2 span. During that
request, `tasks` writes `Sending notification` and `notifier` writes
`Notification received` using ordinary `logging.getLogger(__name__).info(...)`.
Both applications enable `Composition(features=features, request_logging=True)`, adding
`http.request.completed` records with structured request fields and server-span
IDs. `--no-access-log` disables Uvicorn's additional access lines.

There is one log output path: Python logging -> the application-owned OTel
`LoggingHandler` -> OTLP/HTTP -> the LGTM collector -> Loki. Traces use OTLP/HTTP
through the same collector to Tempo. No stdout scraper or second Loki handler is
needed. The demo installs the Python handler once at startup, routes
`uvicorn.error` through it without a duplicate console handler, and uses
`Telemetry(logs=False)` to disable native FastAPI exception log events. Internally
consumed Canon exceptions remain diagnostic; propagated exceptions have one
Uvicorn traceback. Canon itself never configures process-wide logging.

To find the correlated records:

1. Open [Grafana](http://localhost:3000) and select **Explore -> Loki**. Wait a
   few seconds for the batch exporters, then run
   `{service_name="notifier"} |= "Notification received"` (last 15 minutes).
2. Expand that line and select **View trace** on its `trace_id` field. This opens
   the distributed trace in Tempo, including the `tasks` and `notifier` spans.
3. On a span, select **Logs for this span**. The provisioned query is
   `{service_name=~"tasks|notifier"} | trace_id = "<trace-id>"`. It deliberately
   filters by trace across both services, so the results contain the two service
   messages and both completion records for exactly this request.
4. Expand either completion record to inspect `method`, `route`, `status`,
   `duration_ms`, `trace_id` and `span_id`. Normal task routes also show
   `problem_code` when a Canon problem is returned, plus repository/SQL spans.

The mounted [Grafana provisioning](grafana-datasources.yml) configures both
Loki derived fields and Tempo `tracesToLogsV2`; the two-second time margin includes
completion records around the span boundary. It follows
[Grafana's correlation configuration](https://grafana.com/docs/grafana/latest/datasources/tempo/configure-tempo-data-source/configure-trace-to-logs/).
The collector is local development infrastructure using
[Grafana's LGTM image](https://github.com/grafana/docker-otel-lgtm).

The demo owns an explicit SDK provider per service and shuts it down after
Dishka closes engines/clients. It exports traces and Python logs, uses parent-based
sampling at ratio 1.0 and never sets global providers. `/health` is excluded from
tracing; request logging remains independent of telemetry exclusions. Override
`NOTIFIER_URL`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or
`OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` for other local ports. Infrastructure providers
receive the application's `Telemetry` from Dishka. Canon's adapters instrument
only the owned HTTPX2 client and SQLAlchemy engine, without global factory hooks.
Resources and SDK providers remain application-owned.

Stop the collector with:

```console
docker compose -f examples/reference-service/compose.telemetry.yml down
```
