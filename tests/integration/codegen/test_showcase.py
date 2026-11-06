"""Execute the getting-started path with its real declarations and generated SDK."""

import asyncio
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from examples.showcase.app import app
from fastapi_canon.codegen.config import load_config
from fastapi_canon.codegen.generate import generate
from tests.unit.integrations.webrtc.server import serve

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples/showcase"


def test_showcase_source_check_covers_catalog_declarations() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "check",
            "errors",
            "examples/showcase/features/catalog",
            "--strict",
            "--require-complete",
            "--format",
            "json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert len(report["operations"]) == 2
    assert report["contract_gaps"] == report["incomplete_operations"] == 0


def test_showcase_composition_preflight_is_complete() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fastapi_canon",
            "check",
            "composition",
            "examples.showcase.app:composition",
            "--require-complete",
            "--fail-on",
            "warning",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "providers: passed" in result.stdout
    assert "http.contracts: passed" in result.stdout


@pytest.mark.asyncio
async def test_showcase_generated_client_and_saved_contract_have_no_drift(
    tmp_path: Path,
) -> None:
    config = load_config(EXAMPLE / "fastapi-canon.toml")
    contract = tmp_path / "contracts/openapi.json"
    generated = tmp_path / "dist/generated/python/catalog_client"
    config = replace(
        config,
        sources=tuple(replace(source, output=contract) for source in config.sources),
        targets=tuple(replace(target, output=generated) for target in config.targets),
    )
    assert generate(config)[0]
    assert contract.read_bytes() == (EXAMPLE / "contracts/openapi.json").read_bytes()
    assert generate(config, check=True) == (False, "")
    assert generate(config, check=True, offline=True) == (False, "")
    consumer = tmp_path / "consume.py"
    shutil.copy2(EXAMPLE / "consume.py", consumer)
    async with serve(app) as websocket_url:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(consumer),
            "--base-url",
            websocket_url.replace("ws://", "http://", 1),
            cwd=tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=25)
            assert process.returncode == 0, stdout.decode() + stderr.decode()
            assert [json.loads(line) for line in stdout.splitlines()] == [
                {"id": "coffee", "available": 3},
                {"code": "product_not_found", "status": 404},
            ]
        finally:
            if process.returncode is None:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=5)
