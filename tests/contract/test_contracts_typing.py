import subprocess
import sys

import pytest

# Each consumer: (kinds expression, features expression, type-checks cleanly).
_CONSUMERS = {
    "object_references": ("ContractKind.OPENRPC", "feature", True),
    "string_kind": ('"openrpc"', "feature", False),
    "feature_name": ("ContractKind.OPENRPC", '"browser"', False),
}


@pytest.fixture(scope="module")
def mypy_report(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Type-check every consumer in one cold mypy run instead of one per case."""
    root = tmp_path_factory.mktemp("contract_consumers")
    for name, (kinds, features, _) in _CONSUMERS.items():
        (root / f"{name}.py").write_text(
            "from fastapi import FastAPI\n"
            "from fastapi_canon import Composition, Feature, export_contracts\n"
            "from fastapi_canon.contracts import ContractKind, ContractSelection\n"
            'feature = Feature(name="browser")\n'
            "app = Composition(features=(feature,)).build(FastAPI())\n"
            f"export_contracts(app, kinds=({kinds},), "
            f"selection=ContractSelection(features=({features},)))\n",
            encoding="utf-8",
        )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--follow-imports=silent",
            "--cache-dir",
            str(root / ".mypy_cache"),
            *(f"{name}.py" for name in _CONSUMERS),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        # A cold cache analyzes FastAPI and Pydantic; shared CI runners need headroom.
        timeout=180,
        check=False,
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("consumer", tuple(_CONSUMERS))
def test_contract_export_consumer_typechecks_object_references(
    mypy_report: str, consumer: str
) -> None:
    errors = [
        line
        for line in mypy_report.splitlines()
        if line.startswith(f"{consumer}.py:") and ": error:" in line
    ]

    if _CONSUMERS[consumer][2]:
        assert errors == [], mypy_report
    else:
        assert errors, mypy_report
        assert all("[arg-type]" in line for line in errors), mypy_report
