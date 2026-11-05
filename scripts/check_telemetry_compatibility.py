"""Exercise Canon's private FastAPI bridge from wheels in fresh environments."""

import argparse
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--python", default="3.12")
    parser.add_argument("--fastapi", action="append")
    options = parser.parse_args()
    if options.verify:
        _verify()
        return
    output = _ROOT / "dist" / "telemetry-compatibility"
    _run("uv", "build", "--out-dir", str(output))
    wheel = output / f"fastapi_canon-{version('fastapi-canon')}-py3-none-any.whl"
    versions = options.fastapi or list(dict.fromkeys(("0.143.0", version("fastapi"))))
    for selected in versions:
        with tempfile.TemporaryDirectory(
            prefix="canon-fastapi-telemetry-"
        ) as temporary:
            environment = Path(temporary) / "venv"
            _run("uv", "venv", "--python", options.python, str(environment))
            python = environment / (
                "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
            )
            _run(
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                f"{wheel}[otel]",
                f"fastapi=={selected}",
                "opentelemetry-sdk>=1.40,<2",
                "httpx2>=2.12,<3",
            )
            _run(str(python), "-I", str(Path(__file__).resolve()), "--verify")
    print(f"Native FastAPI telemetry compatibility passed: {', '.join(versions)}")


def _verify() -> None:
    from dishka import FromDishka, Provider, Scope
    from dishka.integrations.fastapi import inject
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapi_canon import CanonRouter, Composition, Feature
    from fastapi_canon.integrations.opentelemetry import Telemetry, instrument
    from opentelemetry import trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import Histogram, InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    class Port:
        async def ping(self) -> str:
            return "pong"

    exporter = InMemorySpanExporter()
    tracer = TracerProvider()
    tracer.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    previous = trace.get_tracer_provider()
    calls = []
    try:
        provider = Provider()
        provider.provide(Port, scope=Scope.REQUEST)
        router = CanonRouter()

        @router.get("/{name}")
        @inject
        async def endpoint(name: str, port: FromDishka[Port]) -> str:
            return await port.ping()

        def exclude(scope) -> bool:
            calls.append(scope["path"])
            return scope["path"] == "/excluded"

        app = FastAPI(telemetry={"auto_configure": True})
        configuration = app._telemetry
        options = Telemetry(
            tracer_provider=tracer,
            meter_provider=meter,
            logs=False,
            port_metrics=True,
            exclude=exclude,
        )
        composition = Composition(
            features=(
                Feature(
                    name="compatibility",
                    routers=[router],
                    providers=[provider],
                    instrumentation=instrument(Port),
                ),
            ),
            telemetry=options,
        )
        composition.build(app)
        composition.build(app)
        assert app._telemetry is configuration
        assert configuration["auto_configure"] is False
        assert configuration["meter_provider"] is meter
        with TestClient(app) as client:
            assert client.get("/excluded").json() == "pong"
            assert not exporter.get_finished_spans()
            assert client.get("/included").json() == "pong"
        spans = exporter.get_finished_spans()
        assert len([span for span in spans if span.name == "GET /{name}"]) == 1
        assert len([span for span in spans if span.name == "Port.ping"]) == 1
        data = reader.get_metrics_data()
        assert data is not None
        counts = {
            metric.name: sum(point.count for point in metric.data.data_points)
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if isinstance(metric.data, Histogram)
        }
        assert (
            counts["http.server.request.duration"] == counts["canon.port.duration"] == 1
        )
        assert calls == ["/excluded", "/included"]
        assert trace.get_tracer_provider() is previous
        with tracer.get_tracer("owner").start_as_current_span("owner"):
            pass
        assert exporter.get_finished_spans()[-1].name == "owner"
        # Minimum FastAPI can expose telemetry route contexts without a cached
        # regex. Repeated builds and collision checks must retain converter rules.
        app.get("/{alias:str}", operation_id="late.alias")(lambda alias: "late")
        report = composition.check(app)
        assert report.has_errors
        [issue] = report.issues
        assert issue.code == "canon.routing.E003"
        assert issue.locations == ("GET /{name}", "GET /{alias}")
    finally:
        tracer.shutdown()
        meter.shutdown()
    print(f"Installed-wheel bridge passed with FastAPI {version('fastapi')}")


def _run(*command: str) -> None:
    subprocess.run(command, check=True, cwd=_ROOT, timeout=180)


if __name__ == "__main__":
    _main()
