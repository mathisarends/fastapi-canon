import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts/benchmark_tests.py"


def test_benchmark_records_real_test_phases_fixtures_and_subprocesses(
    tmp_path: Path,
) -> None:
    source = tmp_path / "test_sample.py"
    source.write_text(
        "import subprocess\n"
        "import sys\n"
        "import pytest\n"
        "@pytest.fixture(scope='session')\n"
        "def sample(): return 42\n"
        "def test_sample(sample):\n"
        "    subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
        "    assert sample == 42\n",
        encoding="utf-8",
    )
    output = tmp_path / "benchmark.json"
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--rounds",
            "2",
            "--output",
            str(output),
            "--",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text("utf-8"))
    assert report["pytest_args"] == [str(source)]
    assert report["median_seconds"] > 0
    assert len(report["rounds"]) == 2
    measured = report["rounds"][0]
    assert measured["outcomes"] == {"passed": 1}
    assert measured["collection_seconds"] > 0
    assert measured["subprocesses"] == {Path(sys.executable).name: 1}
    assert {phase["phase"] for phase in measured["phases"]} == {
        "setup",
        "call",
        "teardown",
    }
    sample_fixtures = [
        item for item in measured["fixtures"] if item["name"] == "sample"
    ]
    assert len(sample_fixtures) == 1
    assert sample_fixtures[0]["scope"] == "session"


def test_benchmark_preserves_failures_and_stops_repeating(tmp_path: Path) -> None:
    source = tmp_path / "test_sample.py"
    source.write_text("def test_failure(): assert False\n", encoding="utf-8")
    output = tmp_path / "benchmark.json"
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--rounds",
            "3",
            "--output",
            str(output),
            "--",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(output.read_text("utf-8"))
    assert len(report["rounds"]) == 1
    assert report["rounds"][0]["exit_code"] == 1
    assert report["rounds"][0]["outcomes"] == {"failed": 1}
