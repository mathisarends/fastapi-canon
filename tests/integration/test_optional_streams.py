"""The core package must work even when the optional dependency is absent."""

import subprocess
import sys
import textwrap


def test_core_import_runtime_and_missing_extra_diagnostic() -> None:
    script = textwrap.dedent("""\
        import sys
        from importlib.abc import MetaPathFinder

        class NoOptionalTools(MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + ".")
                       for name in ("jinja2", "jsonschema", "aiortc", "opentelemetry", "fastapi.sse")):
                    raise ModuleNotFoundError("Extension deliberately unavailable", name=fullname)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        # FastAPI itself may import native SSE and telemetry support at startup.
        sys.modules.pop("fastapi.sse", None)
        sys.meta_path.insert(0, NoOptionalTools())
        from fastapi_canon import APIRouter, Composition, Feature
        from http import HTTPMethod
        import fastapi_canon.integrations

        router = APIRouter()
        @router.api_route("/health", methods=[HTTPMethod.GET])
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        app = Composition(features=(Feature(name="health", routers=[router]),)).build(FastAPI())
        with TestClient(app) as client:
            assert client.get("/health").json() == {"status": "ok"}
        assert "/health" in app.openapi()["paths"]
        assert not any(name.startswith("fastapi_canon.integrations.sse") for name in sys.modules)
        assert not any(name.startswith("fastapi_canon.integrations.webrtc") for name in sys.modules)
        try:
            router.sse("/events")
        except ModuleNotFoundError as exc:
            assert exc.name == "fastapi.sse"
            assert "fastapi-canon[sse]" in str(exc)
        else:
            raise AssertionError("The integration must require the optional dependency")
        from fastapi_canon.integrations.webrtc import SessionCapacity
        from fastapi_canon.integrations.webrtc.engines import Engine
        from fastapi_canon.integrations.websockets import RpcChannel
        from fastapi_canon.cli import main
        for args in (["--help"], ["websockets", "--help"], ["webrtc", "--help"]):
            try:
                main(args)
            except SystemExit as result:
                assert result.code == 0
        assert not any(name.startswith("aiortc") for name in sys.modules)
    """)
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
