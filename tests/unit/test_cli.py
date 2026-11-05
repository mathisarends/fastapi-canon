import subprocess
import sys
from pathlib import Path

import pytest

from fastapi_canon import __version__
from fastapi_canon.cli import main


@pytest.mark.parametrize(
    "transport", ["sse", "websockets", "webrtc", "contracts", "generate", "skill"]
)
def test_subcommand_help_uses_the_fastapi_canon_command(
    transport: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([transport, "--help"])
    assert exit_info.value.code == 0
    assert f"fastapi-canon {transport}" in capsys.readouterr().out


def test_version_is_shared_by_all_features() -> None:
    from fastapi_canon.integrations.sse import __version__ as sse_version
    from fastapi_canon.integrations.webrtc import __version__ as webrtc_version
    from fastapi_canon.integrations.websockets import __version__ as websockets_version

    assert sse_version == webrtc_version == websockets_version == __version__


@pytest.mark.parametrize("kind", ["errors", "composition"])
def test_check_help_describes_the_selected_subcommand(
    kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["check", kind, "--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert f"usage: fastapi-canon check {kind}" in output
    assert ("--strict" if kind == "errors" else "--require-complete") in output


def test_check_errors_missing_roots_uses_the_public_command_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["check", "errors"])
    assert exit_info.value.code == 2
    assert "usage: fastapi-canon check errors" in capsys.readouterr().err


def test_check_help_lists_its_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["check", "--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "errors" in output and "composition" in output


def test_module_entry_point_checks_errors_without_importing_application(
    tmp_path: Path,
) -> None:
    (tmp_path / "api.py").write_text(
        "from fastapi_canon import CanonRouter\n"
        "raise RuntimeError('must not import')\n"
        "router = CanonRouter()\n"
        "@router.get('/')\n"
        "def endpoint() -> str: return 'ok'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-I", "-m", "fastapi_canon", "check", "errors", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Analyzed 1 operations" in result.stdout


def test_skill_install_restores_previous_tree_when_replacement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "skill"
    destination.mkdir()
    existing = destination / "SKILL.md"
    existing.write_text("Previous skill", encoding="utf-8")
    rename = Path.rename

    def fail_install(path: Path, target: str | Path) -> Path:
        if path.name == "skill" and Path(target) == destination:
            raise PermissionError("Destination is busy")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_install)

    result = main(["skill", "install", "--destination", str(destination), "--force"])

    assert result == 2
    assert "Destination is busy" in capsys.readouterr().err
    assert existing.read_text("utf-8") == "Previous skill"
    assert list(destination.iterdir()) == [existing]
    assert list(tmp_path.iterdir()) == [destination]


def test_generate_help_explains_selection_json_and_exit_policy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _help_text(["generate", "--help"], capsys)
    assert "repeat for several targets" in text
    assert "default: all targets" in text
    assert "text (default)" in text and "versioned json" in text
    assert "implies --check" in text
    assert "without importing application code" in text
    assert "live sources import application code" in text
    assert "lifespan is never entered" in text
    assert "1 drift" in text and "2 invalid configuration" in text


def test_composition_help_explains_target_defaults_threshold_and_completeness(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _help_text(["check", "composition", "--help"], capsys)
    assert "composition instance" in text and "dotted attributes" in text
    assert "synchronous factory called without arguments" in text
    assert "default: a fresh fastapi app" in text
    assert "error (default)" in text and "include warnings" in text
    assert "exit 2 if a check is skipped or incomplete" in text
    assert "not proof of runtime exception coverage" in text
    assert "lifespan is never entered" in text


def test_inspect_help_explains_automatic_factory_and_snapshot_scope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _help_text(["inspect", "--help"], capsys)
    assert "installed http composition" in text
    assert "module:app or module:factory" in text
    assert "compatibility flag" in text and "accepted automatically" in text
    assert "json snapshot/check report" in text
    assert "imports and synchronous app factories execute application code" in text
    assert "lifespan is never entered" in text
    assert "2 invalid target" in text


def test_error_check_help_distinguishes_independent_source_policies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _help_text(["check", "errors", "--help"], capsys)
    assert "without importing application code" in text
    assert "can001/can002 missing mappings or declarations" in text
    assert "can003 incomplete analysis or no supported operations" in text
    assert "independent of --strict" in text
    assert "not proof of all runtime exception paths" in text
    assert "2 invalid roots, source or arguments" in text


def test_source_read_failure_uses_the_installed_command_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main(["check", "errors", str(tmp_path)])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert message.startswith("fastapi-canon check errors:")
    assert "broken.py" in message


def _help_text(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 0
    return " ".join(capsys.readouterr().out.lower().split())
