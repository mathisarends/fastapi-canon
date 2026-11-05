import json
import subprocess
import sys

import pytest

from fastapi_canon.cli import main


@pytest.mark.parametrize(
    "code",
    [
        "CAN001",
        "CAN002",
        "CAN003",
        "canon.composition.E001",
        "canon.routing.E001",
        "canon.routing.E002",
        "canon.routing.E003",
        "canon.routing.E004",
        "canon.mcp.E001",
        "canon.mcp.E002",
        "canon.handlers.E001",
        "canon.errors.E001",
        "canon.providers.E001",
        "canon.providers.E002",
        "canon.providers.E003",
        "canon.providers.E004",
        "canon.description.E001",
        "canon.contracts.E001",
        "canon.checks.E001",
        "canon.codegen.I001",
        "canon.codegen.W001",
        "canon.codegen.W002",
        "canon.codegen.E001",
        "canon.codegen.E002",
    ],
)
def test_builtin_codes_have_versioned_meaning_example_repair_and_guide(
    code: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["explain", code, "--format", "json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["schema_version"] == 1 and report["code"] == code
    assert report["meaning"].strip()
    assert report["example"].strip()
    assert report["repair"].strip()
    assert report["guide"].startswith(
        "https://github.com/mathisarends/fastapi-canon/blob/main/docs/"
    )


@pytest.mark.parametrize("code", ["CAN001", "CAN003", "canon.routing.E002"])
def test_text_explanations_keep_existing_code_names(
    code: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["explain", code]) == 0
    output = capsys.readouterr().out
    assert output.startswith(code + "\n")
    assert "Example:" in output and "Repair:" in output
    assert "Guide: https://github.com/" in output


@pytest.mark.parametrize("code", ["CAN999", "projects.W001"])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_unknown_or_application_codes_return_actionable_exit_two(
    code: str,
    output_format: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["explain", code, "--format", output_format]) == 2
    captured = capsys.readouterr()
    if output_format == "json":
        report = json.loads(captured.out)
        assert report["schema_version"] == 1 and report["code"] == code
        message = report["error"]
        assert captured.err == ""
    else:
        message = captured.err
        assert captured.out == ""
    assert code in message
    assert "fastapi-canon explain --help" in message
    assert "hint supplied by their check" in message


def test_explanation_and_help_keep_optional_runtimes_and_apps_unimported() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "\n".join(
                [
                    "import sys",
                    "from fastapi_canon.cli import main",
                    "assert main(['explain', 'CAN003']) == 0",
                    "try:",
                    "    main(['explain', '--help'])",
                    "except SystemExit as error:",
                    "    assert error.code == 0",
                    "assert not any(name in sys.modules for name in ('aiortc', 'jinja2', 'jsonschema', 'fastmcp', 'opentelemetry.sdk', 'examples.canonical_errors.app'))",
                ]
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CAN003" in result.stdout and "fastapi-canon explain" in result.stdout
