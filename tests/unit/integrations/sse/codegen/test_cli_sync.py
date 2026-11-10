import json
from pathlib import Path

import pytest
from fastapi_canon.integrations.sse.codegen.cli import main

# Reviewed acceptances for the Canon problem schemas' URI formats.
ALLOWED = (
    Path(__file__).parents[5] / "tests/fixtures/sse/agent_runs.python.diagnostics.json"
).as_posix()

CONFIG = f"""
[tool.fastapi-canon.sse]
app = "examples.sse.agent_runs:app"
schema = "contract/openapi.json"

[[tool.fastapi-canon.sse.clients]]
language = "typescript"
output = "web/generated"

[[tool.fastapi-canon.sse.clients]]
language = "python"
output = "py/generated"
python-http-client = "httpx2"
strict = true
allow-diagnostics = "{ALLOWED}"
"""


def test_standalone_operation_selection_generates_and_checks_one_stream(tmp_path):
    schema = Path(__file__).parents[5] / "tests/fixtures/sse/agent_runs.openapi.json"
    output = tmp_path / "client"
    arguments = [
        "--schema",
        str(schema),
        "--language",
        "python",
        "--output",
        str(output),
        "--include-operation",
        "stream_run_events",
    ]
    assert main(["generate", *arguments]) == 0
    client = (output / "agent_runs/api.py").read_text()
    assert "stream_run_events" in client and "stream_prompt" not in client
    assert main(["check", *arguments]) == 0
    assert main(["check", *arguments, "--include-operation", "missing"]) == 2


def test_sync_writes_schema_and_every_client_then_checks_clean(tmp_path, capsys):
    config = tmp_path / "pyproject.toml"
    config.write_text(CONFIG, encoding="utf-8")
    args = ["sync", "--config", str(config)]
    assert main([*args, "--check"]) == 1
    assert not (tmp_path / "contract").exists()
    assert main(args) == 0
    assert (tmp_path / "contract/openapi.json").is_file()
    assert (tmp_path / "web/generated/index.ts").is_file()
    manifest = json.loads(
        (tmp_path / "py/generated/.fastapi-canon-sse.json").read_text("utf-8")
    )
    assert manifest["generation"]["python_http_client"] == "httpx2"
    capsys.readouterr()
    assert main([*args, "--check", "--diff"]) == 0
    assert capsys.readouterr().out == ""


def test_sync_accepts_legacy_config_with_a_visible_deprecation_warning(
    tmp_path, capsys
):
    config = tmp_path / "pyproject.toml"
    config.write_text(
        CONFIG.replace("tool.fastapi-canon.sse", "tool.fastapi-streams"),
        encoding="utf-8",
    )
    args = ["sync", "--config", str(config)]
    assert main(args) == 0
    warning = capsys.readouterr().err
    assert "DeprecationWarning: [tool.fastapi-streams]" in warning
    assert "[tool.fastapi-canon.sse]" in warning
    assert (tmp_path / "py/generated/.fastapi-canon-sse.json").is_file()
    assert main([*args, "--check"]) == 0


def test_sync_prefers_canon_config_when_both_tables_exist(tmp_path, capsys):
    config = tmp_path / "pyproject.toml"
    config.write_text(
        CONFIG + '\n[tool.fastapi-streams]\nschema = "unused.json"\n',
        encoding="utf-8",
    )
    assert main(["sync", "--config", str(config)]) == 0
    assert "DeprecationWarning" in capsys.readouterr().err
    assert (tmp_path / "contract/openapi.json").is_file()
    assert not (tmp_path / "unused.json").exists()


def test_sync_check_reports_every_stale_target_with_a_diff(tmp_path, capsys):
    config = tmp_path / "pyproject.toml"
    config.write_text(CONFIG, encoding="utf-8")
    assert main(["sync", "--config", str(config)]) == 0
    types = tmp_path / "web/generated/types.ts"
    types.write_text(types.read_text("utf-8") + "// local edit\n", encoding="utf-8")
    (tmp_path / "py/generated/stale.py").write_text("x = 1\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["sync", "--config", str(config), "--check", "--diff"]) == 1
    captured = capsys.readouterr()
    assert "Out of date:" in captured.err and "types.ts" in captured.err
    assert "-// local edit" in captured.out
    assert "/web/generated/types.ts" in captured.out
    assert "stale.py" not in captured.err  # Unowned files are never reported.
    assert types.read_text("utf-8").endswith("// local edit\n")


def test_generate_check_diff_shows_new_and_changed_files(tmp_path, capsys):
    schema = tmp_path / "schema.json"
    output = tmp_path / "sdk"
    assert (
        main(
            ["schema", "--app", "examples.sse.agent_runs:app", "--output", str(schema)]
        )
        == 0
    )
    args = ["generate", "--schema", str(schema), "--output", str(output), "--check"]
    capsys.readouterr()
    assert main([*args, "--diff"]) == 1
    out = capsys.readouterr().out
    assert "--- /dev/null" in out and "+++ b/" in out and "index.ts" in out
    assert not output.exists()


def test_schema_and_app_checks_print_contract_diffs(tmp_path, capsys):
    schema = tmp_path / "schema.json"
    app = ["--app", "examples.sse.agent_runs:app"]
    assert main(["schema", *app, "--output", str(schema)]) == 0
    document = json.loads(schema.read_text("utf-8"))
    document["info"]["title"] = "Renamed"
    schema.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", "utf-8")
    capsys.readouterr()
    assert main(["schema", *app, "--output", str(schema), "--check", "--diff"]) == 1
    assert '-    "title": "Renamed"' in capsys.readouterr().out
    check = ["check", *app, "--schema", str(schema), "--output", str(tmp_path / "x")]
    assert main([*check, "--diff"]) == 1
    assert '-    "title": "Renamed"' in capsys.readouterr().out


def test_diff_requires_check(tmp_path, capsys):
    schema = tmp_path / "schema.json"
    args = ["schema", "--app", "examples.sse.agent_runs:app", "--output", str(schema)]
    assert main([*args, "--diff"]) == 2
    assert "--diff requires --check" in capsys.readouterr().err
    assert not schema.exists()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ("[tool.other]\n", "no [tool.fastapi-canon.sse] table"),
        ('[tool.fastapi-canon.sse]\nschema = "s.json"\nextra = 1\n', "Unknown"),
        ('[tool.fastapi-canon.sse]\napp = "m:app"\n', "requires schema"),
        ('[tool.fastapi-canon.sse]\nschema = "s.json"\n', "neither app nor clients"),
        (
            '[tool.fastapi-canon.sse]\nschema = "s.json"\n'
            '[[tool.fastapi-canon.sse.clients]]\nlanguage = "go"\noutput = "x"\n',
            "language must be",
        ),
        (
            '[tool.fastapi-canon.sse]\nschema = "s.json"\n'
            '[[tool.fastapi-canon.sse.clients]]\nlanguage = "python"\n',
            "requires output",
        ),
        (
            '[tool.fastapi-canon.sse]\nschema = "s.json"\n'
            '[[tool.fastapi-canon.sse.clients]]\nlanguage = "python"\n'
            'output = "x"\nstrict = "yes"\n',
            "strict must be a boolean",
        ),
        ("[tool.fastapi-canon.sse\n", "Invalid TOML"),
    ],
)
def test_sync_rejects_invalid_configuration(tmp_path, capsys, config, message):
    path = tmp_path / "pyproject.toml"
    path.write_text(config, encoding="utf-8")
    assert main(["sync", "--config", str(path)]) == 2
    assert message in capsys.readouterr().err
