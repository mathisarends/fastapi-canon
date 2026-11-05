import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("agent", [".agents", ".claude"])
def test_install_bundled_skill_in_consumer_project(tmp_path: Path, agent: str) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "fastapi_canon", "skill", "install"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    skill = tmp_path / agent / "skills/build-canon-backend"
    assert {
        path.relative_to(skill).as_posix()
        for path in skill.rglob("*")
        if path.is_file()
    } == {
        "SKILL.md",
        "agents/openai.yaml",
        "references/application-layout.md",
        "references/components.md",
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
    assert not (tmp_path / ".claude").exists()


@pytest.mark.parametrize("agent", [".agents", ".claude"])
def test_default_install_checks_all_destinations_before_writing(
    tmp_path: Path, agent: str
) -> None:
    destination = tmp_path / agent / "skills/build-canon-backend"
    destination.mkdir(parents=True)
    existing = destination / "SKILL.md"
    existing.write_text("User customizations", encoding="utf-8")

    result = _run_skill(tmp_path)

    assert result.returncode == 2
    assert "--force" in result.stderr
    assert existing.read_text("utf-8") == "User customizations"
    assert sorted(
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")
    ) == [
        agent,
        f"{agent}/skills",
        f"{agent}/skills/build-canon-backend",
        f"{agent}/skills/build-canon-backend/SKILL.md",
    ]


def test_force_refreshes_both_copies_and_restores_missing_resources(
    tmp_path: Path,
) -> None:
    assert _run_skill(tmp_path).returncode == 0
    agents = tmp_path / ".agents/skills/build-canon-backend"
    claude = tmp_path / ".claude/skills/build-canon-backend"
    expected = _contents(agents)
    (agents / "SKILL.md").write_text("Old skill", encoding="utf-8")
    (agents / "references/components.md").unlink()
    (claude / "references/obsolete.md").write_text("Obsolete", encoding="utf-8")
    unrelated = claude.parent / "another-skill/SKILL.md"
    unrelated.parent.mkdir()
    unrelated.write_text("Keep me", encoding="utf-8")

    result = _run_skill(tmp_path, "--force")

    assert result.returncode == 0, result.stderr
    assert _contents(agents) == expected
    assert _contents(claude) == expected
    assert unrelated.read_text("utf-8") == "Keep me"
    assert _run_skill(tmp_path, "--force").returncode == 0
    assert _contents(agents) == _contents(claude) == expected


def test_force_installs_a_missing_agent_directory(tmp_path: Path) -> None:
    agents = tmp_path / ".agents/skills/build-canon-backend"
    agents.mkdir(parents=True)
    (agents / "SKILL.md").write_text("Old skill", encoding="utf-8")

    result = _run_skill(tmp_path, "--force")

    assert result.returncode == 0, result.stderr
    assert _contents(agents) == _contents(
        tmp_path / ".claude/skills/build-canon-backend"
    )
    assert (agents / "references/components.md").is_file()


def test_force_refreshes_only_a_custom_destination(tmp_path: Path) -> None:
    destination = tmp_path / "custom skill"
    destination.mkdir()
    (destination / "old.md").write_text("Old", encoding="utf-8")

    result = _run_skill(tmp_path, "--destination", str(destination), "--force")

    assert result.returncode == 0, result.stderr
    assert (destination / "SKILL.md").is_file()
    assert (destination / "references/components.md").is_file()
    assert not (destination / "old.md").exists()
    assert not (tmp_path / ".agents").exists()
    assert not (tmp_path / ".claude").exists()


def test_force_stages_all_resources_before_changing_existing_skills(
    tmp_path: Path,
) -> None:
    agents = tmp_path / ".agents/skills/build-canon-backend"
    agents.mkdir(parents=True)
    existing = agents / "SKILL.md"
    existing.write_text("Keep on failure", encoding="utf-8")
    (tmp_path / ".claude").write_text("Not a directory", encoding="utf-8")

    result = _run_skill(tmp_path, "--force")

    assert result.returncode == 2
    assert "Could not install skill" in result.stderr
    assert existing.read_text("utf-8") == "Keep on failure"
    assert list(agents.iterdir()) == [existing]
    assert list(agents.parent.iterdir()) == [agents]


@pytest.mark.parametrize("destination", [".", ".."])
def test_force_refuses_to_replace_the_project_or_its_parent(
    tmp_path: Path, destination: str
) -> None:
    marker = tmp_path / "keep.txt"
    marker.write_text("Project data", encoding="utf-8")

    result = _run_skill(tmp_path, "--destination", destination, "--force")

    assert result.returncode == 2
    assert "contains the project or bundle" in result.stderr
    assert marker.read_text("utf-8") == "Project data"
    assert list(tmp_path.iterdir()) == [marker]


def _run_skill(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "fastapi_canon", "skill", "install", *arguments],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _contents(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }
