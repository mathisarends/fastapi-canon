import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("kinds", "features", "valid"),
    [
        ("ContractKind.OPENRPC", "feature", True),
        ('"openrpc"', "feature", False),
        ("ContractKind.OPENRPC", '"browser"', False),
    ],
)
def test_contract_export_consumer_typechecks_object_references(
    tmp_path: Path, kinds: str, features: str, valid: bool
) -> None:
    consumer = tmp_path / "consumer.py"
    consumer.write_text(
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
            str(consumer),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == (0 if valid else 1), result.stdout + result.stderr
    if not valid:
        assert "[arg-type]" in result.stdout
