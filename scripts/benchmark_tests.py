"""Measure the real pytest suite in fresh processes; keep reports in dist/."""

import argparse
import hashlib
import json
import platform
import shlex
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("dist/test-benchmark.json"))
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    selected = args.pytest_args
    if selected[:1] == ["--"]:
        selected = selected[1:]
    if args.worker:
        recorder = _Recorder()
        sys.addaudithook(recorder.audit)
        started = perf_counter()
        exit_code = pytest.main(["-q", "--durations=30", *selected], plugins=[recorder])
        args.worker.write_text(
            json.dumps(
                {
                    "elapsed_seconds": perf_counter() - started,
                    "exit_code": int(exit_code),
                    "collection_seconds": recorder.collection_seconds,
                    "outcomes": dict(recorder.outcomes),
                    "subprocesses": dict(recorder.subprocesses),
                    "fixtures": recorder.fixtures,
                    "phases": recorder.phases,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return int(exit_code)
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    report: dict[str, Any] = {
        "commit": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "python": sys.version,
        "platform": platform.platform(),
        "pytest": version("pytest"),
        "lock_sha256": hashlib.sha256((_ROOT / "uv.lock").read_bytes()).hexdigest(),
        "pytest_args": selected,
        "rounds": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for index in range(args.rounds):
        print(f"Benchmark round {index + 1}/{args.rounds}", flush=True)
        with tempfile.TemporaryDirectory(
            prefix="round-", dir=args.output.parent.resolve()
        ) as temporary:
            worker = Path(temporary) / "round.json"
            started = perf_counter()
            result = subprocess.run(
                [sys.executable, __file__, "--worker", str(worker), "--", *selected],
                cwd=_ROOT,
                check=False,
            )
            if not worker.is_file():
                raise SystemExit(result.returncode or 1)
            measured = json.loads(worker.read_text("utf-8"))
            measured["process_seconds"] = perf_counter() - started
            report["rounds"].append(measured)
        report["median_seconds"] = statistics.median(
            item["process_seconds"] for item in report["rounds"]
        )
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if result.returncode:
            return result.returncode
    print(f"Median: {report['median_seconds']:.2f}s; report: {args.output}")
    return 0


class _Recorder:
    def __init__(self) -> None:
        self.started = perf_counter()
        self.collection_seconds = 0.0
        self.outcomes: Counter[str] = Counter()
        self.subprocesses: Counter[str] = Counter()
        self.fixtures: list[dict[str, Any]] = []
        self.phases: list[dict[str, Any]] = []

    def audit(self, event: str, arguments: tuple[Any, ...]) -> None:
        if event == "subprocess.Popen":
            executable, command = arguments[:2]
            if executable is None:
                executable = (
                    command[0]
                    if isinstance(command, (list, tuple))
                    else shlex.split(command, posix=False)[0].strip('"')
                )
            self.subprocesses[Path(executable).name] += 1

    @pytest.hookimpl
    def pytest_sessionstart(self) -> None:
        self.started = perf_counter()

    @pytest.hookimpl
    def pytest_collection_finish(self) -> None:
        self.collection_seconds = perf_counter() - self.started

    @pytest.hookimpl
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.phases.append(
            {
                "test": report.nodeid,
                "phase": report.when,
                "seconds": report.duration,
                "outcome": report.outcome,
            }
        )
        if report.when == "call" or report.outcome != "passed":
            self.outcomes[report.outcome] += 1

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(self, fixturedef: pytest.FixtureDef[Any]) -> Any:
        started = perf_counter()
        try:
            return (yield)
        finally:
            self.fixtures.append(
                {
                    "name": fixturedef.argname,
                    "scope": fixturedef.scope,
                    "seconds": perf_counter() - started,
                }
            )


def _git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=_ROOT, text=True).strip()


if __name__ == "__main__":
    raise SystemExit(_main())
