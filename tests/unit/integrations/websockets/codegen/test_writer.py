import json
from pathlib import Path

import pytest
from fastapi_canon.integrations.websockets.codegen.writer import MANIFEST, write_files


def _manifest(*files: str) -> str:
    return json.dumps({"files": [*files, MANIFEST]}, indent=2) + "\n"


def test_a_new_run_removes_only_previously_generated_files(tmp_path: Path) -> None:
    output = tmp_path / "client"
    write_files(
        output,
        {
            "api/current.py": "current\n",
            "api/stale.py": "stale\n",
            MANIFEST: _manifest("api/current.py", "api/stale.py"),
        },
    )
    custom = output / "api" / "custom.py"
    custom.write_text("custom\n", encoding="utf-8")

    changed = write_files(
        output,
        {
            "api/current.py": "current\n",
            MANIFEST: _manifest("api/current.py"),
        },
    )

    assert changed == (output / MANIFEST, output / "api" / "stale.py")
    assert not (output / "api" / "stale.py").exists()
    assert custom.read_text(encoding="utf-8") == "custom\n"


def test_check_reports_stale_files_without_changing_anything(tmp_path: Path) -> None:
    output = tmp_path / "client"
    old_manifest = _manifest("stale.py")
    write_files(
        output,
        {"stale.py": "stale\n", MANIFEST: old_manifest},
    )

    changed = write_files(
        output,
        {MANIFEST: _manifest()},
        check=True,
    )

    assert changed == (output / MANIFEST, output / "stale.py")
    assert (output / "stale.py").read_text(encoding="utf-8") == "stale\n"
    assert (output / MANIFEST).read_text(encoding="utf-8") == old_manifest


def test_an_unsafe_manifest_path_is_rejected_before_writing(tmp_path: Path) -> None:
    output = tmp_path / "client"
    write_files(output, {MANIFEST: _manifest("../outside.py")})

    with pytest.raises(ValueError, match="Unsafe generated path"):
        write_files(output, {"new.py": "new\n", MANIFEST: _manifest("new.py")})

    assert not (output / "new.py").exists()


@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("manifest", ["{", "[]", '{"files": [1]}', "{}"])
def test_corrupt_manifest_fails_before_modifying_output(
    tmp_path: Path,
    check: bool,
    manifest: str,
) -> None:
    ownership = tmp_path / MANIFEST
    ownership.parent.mkdir()
    ownership.write_text(manifest, encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid generated manifest"):
        write_files(tmp_path, {"new.py": "new"}, check=check)
    assert ownership.read_text(encoding="utf-8") == manifest
    assert not (tmp_path / "new.py").exists()


@pytest.mark.parametrize(
    "unsafe", ["../outside.py", "a\\outside.py", "C:outside.py", "", "."]
)
def test_new_paths_are_preflighted_before_any_write(
    tmp_path: Path, unsafe: str
) -> None:
    with pytest.raises(ValueError, match=r"generated path|Generated path"):
        write_files(tmp_path, {"safe.py": "safe", unsafe: "unsafe"})
    assert not tuple(tmp_path.iterdir())


def test_failed_atomic_replace_preserves_old_file_and_cleans_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "client.py"
    target.write_text("old", encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_files(tmp_path, {"client.py": "new"})
    assert target.read_text(encoding="utf-8") == "old"
    assert tuple(tmp_path.iterdir()) == (target,)
