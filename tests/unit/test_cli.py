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
