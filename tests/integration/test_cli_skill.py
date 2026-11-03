import subprocess
import sys
from pathlib import Path


def test_install_bundled_skill_in_consumer_project(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "fastapi_canon", "skill", "install"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    skill = tmp_path / ".agents/skills/build-canon-backend"
    assert {
        path.relative_to(skill).as_posix()
        for path in skill.rglob("*")
        if path.is_file()
    } == {
        "SKILL.md",
        "agents/openai.yaml",
        "references/application-layout.md",
        "references/http-feature.md",
        "references/resources.md",
        "references/testing.md",
        "references/transports-and-sdk.md",
    }
    assert "name: build-canon-backend" in (skill / "SKILL.md").read_text("utf-8")


def test_install_refuses_existing_destination_without_changing_it(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "custom-skill"
    destination.mkdir()
    existing = destination / "SKILL.md"
    existing.write_text("User customizations", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "fastapi_canon",
            "skill",
            "install",
            "--destination",
            str(destination),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "Could not install skill" in result.stderr
    assert existing.read_text("utf-8") == "User customizations"
    assert list(destination.iterdir()) == [existing]


def test_install_skill_at_custom_destination(tmp_path: Path) -> None:
    destination = tmp_path / "agent skills/canon"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "fastapi_canon",
            "skill",
            "install",
            "--destination",
            str(destination),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (destination / "SKILL.md").is_file()
    assert (destination / "references/http-feature.md").is_file()
    assert not (tmp_path / ".agents").exists()
