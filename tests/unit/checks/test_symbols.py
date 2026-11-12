import json
from pathlib import Path

import pytest

from fastapi_canon.cli import main


@pytest.mark.parametrize(
    ("prefix", "guard", "complete"),
    [
        ("", "__name__ == '__main__'", True),
        ("", "'__main__' == __name__", True),
        ("__name__ = runtime_name()", "__name__ == '__main__'", False),
    ],
)
def test_import_guards_preserve_domain_error_analysis_and_shadowing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    prefix: str,
    guard: str,
    complete: bool,
) -> None:
    (tmp_path / "api.py").write_text(
        f"""
from fastapi_canon import APIRouter, Error
class Missing(Exception): pass
missing = Error(Missing, status=404, code='missing', title='Missing')
router = APIRouter()
{prefix}
if {guard}:
    start_server()
else:
    declared = [missing]
@router.get('/', raises=declared)
def endpoint(): raise Missing()
""",
        encoding="utf8",
    )
    status = main(
        [
            "check",
            "errors",
            str(tmp_path),
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    [operation] = report["operations"]
    assert operation["complete"] == complete
    assert status == int(not complete)
    assert report["contract_gaps"] == 0
