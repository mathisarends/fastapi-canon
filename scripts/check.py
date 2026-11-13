import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from time import perf_counter

_ROOT = Path(__file__).resolve().parents[1]
_REFERENCE = _ROOT / "examples/reference-service"
_TRANSPORTS = ("sse", "websockets", "webrtc")


def _main() -> None:
    parser = argparse.ArgumentParser(description="Run Canon quality gates")
    parser.add_argument(
        "gate",
        choices=("all", "architecture", "lint", "types", "test", "clients", "browser"),
    )
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args()
    runner = _Runner(args.report_dir)
    gates = (
        ("architecture", "lint", "types", "test", "clients")
        if args.gate == "all"
        else (args.gate,)
    )
    for gate in gates:
        runner.gate = gate
        if gate == "architecture":
            runner.run(
                _ROOT,
                "uv",
                "run",
                "--frozen",
                "smelt",
                "check",
                "--config",
                "smelt.yml",
                "--fail-on",
                "warning",
                python=False,
            )
        elif gate == "lint":
            runner.run(_ROOT, "scripts/check_documentation.py")
            # Moving configs can leave stale import classifications in Ruff's cache.
            runner.run(_ROOT, "-m", "ruff", "check", "--no-cache", ".")
            runner.run(_ROOT, "-m", "ruff", "format", "--no-cache", "--check", ".")
            for transport in _TRANSPORTS:
                source = f"fastapi_canon/integrations/{transport}"
                config = (
                    "--config",
                    f"fastapi_canon/integrations/{transport}/.ruff.toml",
                )
                runner.run(_ROOT, "-m", "ruff", "check", "--no-cache", *config, source)
                runner.run(
                    _ROOT,
                    "-m",
                    "ruff",
                    "format",
                    "--no-cache",
                    "--check",
                    *config,
                    source,
                )
        elif gate == "types":
            # The runnable consumer imports generated, ignored example clients.
            # Materialize them from saved contracts without rewriting those inputs,
            # then verify live drift before checking the consumer's types.
            example = "examples/canonical_errors/fastapi-canon.toml"
            runner.run(
                _ROOT,
                "-m",
                "fastapi_canon",
                "generate",
                "--config",
                example,
                "--offline",
            )
            runner.run(
                _ROOT, "-m", "fastapi_canon", "generate", "--config", example, "--check"
            )
            runner.run(_ROOT, "-m", "mypy")
            runner.run(
                _ROOT,
                "-m",
                "pyright",
                "--project",
                "pyproject.toml",
                "--pythonpath",
                sys.executable,
            )
            runner.run(
                _ROOT,
                "-m",
                "pylint",
                "--rcfile",
                "pyproject.toml",
                "fastapi_canon/integrations/sse",
            )
            runner.run(
                _ROOT, "uv", "build", "--out-dir", "dist/typecheck", python=False
            )
            metadata = tomllib.loads((_ROOT / "pyproject.toml").read_text("utf-8"))
            version = metadata["project"]["version"]
            (wheel,) = (_ROOT / "dist/typecheck").glob(f"fastapi_canon-{version}-*.whl")
            runner.run(
                _ROOT / "dist/typecheck",
                "uv",
                "run",
                "--isolated",
                "--no-project",
                "--no-cache",
                "--python",
                sys.executable,
                "--with",
                str(wheel) + "[codegen,websockets,otel]",
                "--with",
                "pyright==1.1.414",
                "python",
                str(_ROOT / "scripts/websockets/verify_types.py"),
                python=False,
            )
            runner.run(_REFERENCE, "-m", "mypy", "src", "tests")
            _check_webrtc_types(runner)
        elif gate == "test":
            reports = (
                ("--junitxml", str(runner.directory / "python.xml"))
                if runner.directory
                else ()
            )
            runner.run(
                _ROOT,
                "-m",
                "pytest",
                "-q",
                "--cov=fastapi_canon.integrations.webrtc",
                "--cov-branch",
                "--cov-fail-under=85",
                "--durations=30",
                *reports,
            )
            reference_reports = (
                ("--junitxml", str(runner.directory / "reference.xml"))
                if runner.directory
                else ()
            )
            runner.run(_REFERENCE, "-m", "pytest", "-q", *reference_reports)
        elif gate == "clients":
            for name in ("generated_clients", "socket_protocol", "typed_socket"):
                runner.run(
                    _ROOT,
                    "-m",
                    "fastapi_canon.integrations.websockets.codegen.cli",
                    "generate",
                    "--config",
                    f"examples/websockets/{name}/rpcgen.toml",
                    "--check",
                )
            runner.run(_ROOT, "scripts/sse/codegen.py", "--check")
            cli = ("-m", "fastapi_canon.integrations.webrtc.codegen.cli")
            contract = "examples/webrtc/practice/contract.json"
            runner.run(
                _ROOT,
                *cli,
                "contract",
                "--app",
                "examples.webrtc.practice.app:app",
                "--output",
                contract,
                "--check",
            )
            runner.run(
                _ROOT,
                *cli,
                "generate",
                "--contract",
                contract,
                "--client-name",
                "PracticeRtcClient",
                "--output",
                "examples/webrtc/practice/client",
                "--check",
            )
            for task in ("typecheck", "test:coverage"):
                runner.run(_ROOT, _pnpm(), "run", task, python=False)
        elif gate == "browser":
            environment = {**os.environ, "FASTAPI_WEBRTC_PYTHON": sys.executable}
            runner.run(
                _ROOT, _pnpm(), "run", "test:browser", python=False, env=environment
            )


def _check_webrtc_types(runner: "_Runner") -> None:
    # SSE uses pyright and WebSockets uses root mypy; WebRTC has this scoped profile.
    config = """[tool.mypy]
python_version = "3.12"
strict = true
explicit_package_bases = true
exclude = ["node_modules"]
pretty = true
show_error_codes = true
warn_unreachable = true

[[tool.mypy.overrides]]
module = ["av", "av.*"]
ignore_missing_imports = true
"""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "webrtc.toml"
        path.write_text(config, encoding="utf-8")
        runner.run(
            _ROOT,
            "-m",
            "mypy",
            "--config-file",
            str(path),
            "fastapi_canon/integrations/webrtc",
            "tests/unit/integrations/webrtc",
            "tests/integration/integrations/webrtc/engine",
            "tests/contract/integrations/webrtc",
            "examples/webrtc",
        )


def _pnpm() -> str:
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if pnpm is None:
        raise SystemExit(
            "pnpm is required for TypeScript client checks; see docs/contributing.md"
        )
    return pnpm


class _Runner:
    def __init__(self, directory: Path | None) -> None:
        self.directory = directory.resolve() if directory else None
        self.gate = ""
        self.commands: list[dict[str, object]] = []
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        root: Path,
        *arguments: str,
        python: bool = True,
        env: dict[str, str] | None = None,
    ) -> None:
        command = [sys.executable, *arguments] if python else list(arguments)
        print(f"[{root.relative_to(_ROOT)}] {' '.join(command)}", flush=True)
        started = perf_counter()
        completed = subprocess.run(command, cwd=root, check=False, env=env)
        elapsed = perf_counter() - started
        print(f"[{self.gate}] {elapsed:.2f}s; exit {completed.returncode}", flush=True)
        self.commands.append(
            {
                "gate": self.gate,
                "cwd": str(root.relative_to(_ROOT)),
                "command": command,
                "seconds": elapsed,
                "returncode": completed.returncode,
            }
        )
        if self.directory:
            (self.directory / f"{self.gate}.json").write_text(
                json.dumps(
                    {
                        "python": sys.version,
                        "platform": sys.platform,
                        "commands": [
                            item for item in self.commands if item["gate"] == self.gate
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        completed.check_returncode()


if __name__ == "__main__":
    _main()
