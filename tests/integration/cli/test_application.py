"""CLI targets share bounded loading, factory and diagnostic behavior."""

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def application_module(tmp_path: Path) -> Path:
    (tmp_path / "targets.py").write_text(
        dedent("""
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from fastapi import Depends, FastAPI
        from fastapi_canon import APIRouter, Composition, Feature

        calls = 0
        @asynccontextmanager
        async def lifespan(app):
            raise RuntimeError('private-runtime-value')
            yield
        def runtime_dependency():
            raise RuntimeError('private-runtime-value')
        router = APIRouter(namespace='demo')
        @router.get('/ping', dependencies=[Depends(runtime_dependency)])
        def ping() -> str: return 'pong'
        composition = Composition(features=[Feature(name='demo', routers=[router])])
        class Application(FastAPI):
            async def __call__(self, scope, receive, send):
                raise AssertionError('Do not call an app instance')
        def make():
            return composition.build(Application(lifespan=lifespan))
        app = make()
        def factory():
            from factory_support import VALUE
            assert VALUE == 'configured'
            global calls
            calls += 1
            assert calls == 1, 'Factory called twice'
            return make()
        nested = SimpleNamespace(app=app, factory=factory)
        async def async_factory(): return make()
        def awaitable_factory(): return async_factory()
        def required_factory(argument): return make()
        def failing_factory(): raise RuntimeError('private-runtime-value')
        def generator_factory(): yield make()
        async def async_generator_factory(): yield make()
        class AsyncFactory:
            async def __call__(self): return make()
        async_instance = AsyncFactory()
        class OpaqueFactory:
            @property
            def __signature__(self): raise RuntimeError('private-runtime-value')
            def __call__(self): return make()
        opaque_factory = OpaqueFactory()
        """),
        encoding="utf-8",
    )
    (tmp_path / "eager.py").write_text(
        "raise RuntimeError('private-runtime-value')\n", encoding="utf-8"
    )
    (tmp_path / "factory_support.py").write_text(
        "VALUE = 'configured'\n", encoding="utf-8"
    )
    return tmp_path


@pytest.mark.parametrize("kind", ["inspect", "check", "contracts", "generate"])
@pytest.mark.parametrize("attribute", ["nested.app", "nested.factory"])
def test_commands_load_instances_or_one_factory_without_runtime_execution(
    application_module: Path, kind: str, attribute: str
) -> None:
    result = _run(application_module, kind, f"targets:{attribute}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "private-runtime-value" not in result.stdout + result.stderr
    if kind == "inspect":
        report = json.loads(result.stdout)
        assert report["operations"][0]["operation_id"] == "demo.ping"
    elif kind == "check":
        assert json.loads(result.stdout)["complete"]
    elif kind == "contracts":
        document = json.loads(
            (application_module / "contracts/openapi.json").read_text()
        )
        assert document["paths"]["/ping"]["get"]["operationId"] == "demo.ping"
    else:
        assert (application_module / "sdk/.fastapi-canon-sse.json").is_file()


@pytest.mark.parametrize("kind", ["inspect", "check", "contracts", "generate"])
@pytest.mark.parametrize(
    ("reference", "hint"),
    [
        ("eager:app", "import"),
        ("targets:nested.missing", "attribute"),
        ("targets:required_factory", "zero-argument"),
        ("targets:async_factory", "synchronous"),
        ("targets:awaitable_factory", "synchronous"),
        ("targets:generator_factory", "generator"),
        ("targets:async_generator_factory", "generator"),
        ("targets:async_instance", "synchronous"),
        ("targets:opaque_factory", "inspectable"),
        ("targets:failing_factory", "factory"),
    ],
)
def test_invalid_targets_have_actionable_sanitized_diagnostics(
    application_module: Path, kind: str, reference: str, hint: str
) -> None:
    result = _run(application_module, kind, reference)
    assert result.returncode == 2
    output = result.stdout + result.stderr
    assert hint in output.lower()
    assert reference in output
    assert "private-runtime-value" not in output
    assert "Traceback" not in output
    assert "was never awaited" not in output
    assert not (application_module / "contracts").exists()
    assert not (application_module / "sdk").exists()
    if kind in ("inspect", "check"):
        assert json.loads(result.stdout)["error"]


@pytest.mark.parametrize("kind", ["inspect", "check"])
def test_text_loading_errors_have_the_same_sanitized_hint(
    application_module: Path,
    kind: str,
) -> None:
    result = _run(application_module, kind, "eager:app", output_format="text")
    assert result.returncode == 2
    assert "Cannot import 'eager:app' (RuntimeError)" in result.stderr
    assert "runtime configuration" in result.stderr
    assert "private-runtime-value" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_generation_keeps_config_root_available_for_factory_imports(
    application_module: Path,
) -> None:
    generated = _run(application_module, "generate", "targets:nested.factory")
    assert generated.returncode == 0, generated.stderr
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "generate",
            "--config",
            str(application_module / "fastapi-canon.toml"),
            "--check",
        ],
        cwd=application_module.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _run(
    directory: Path,
    kind: str,
    reference: str,
    *,
    output_format: str = "json",
) -> subprocess.CompletedProcess[str]:
    if kind == "inspect":
        arguments = ["inspect", reference, "--format", output_format]
    elif kind == "check":
        arguments = [
            "check",
            "composition",
            "targets:composition",
            "--app",
            reference,
            "--format",
            output_format,
        ]
    elif kind == "contracts":
        arguments = [
            "contracts",
            "export",
            "--app",
            reference,
            "--output-dir",
            "contracts",
        ]
    else:
        (directory / "fastapi-canon.toml").write_text(
            f'[codegen]\nversion = 2\napp = "{reference}"\n'
            '[codegen.targets.streams]\ngenerator = "sse"\n'
            'language = "python"\noutput = "sdk"\n',
            encoding="utf-8",
        )
        arguments = ["generate"]
    return subprocess.run(
        [sys.executable, "-m", "fastapi_canon", *arguments],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
