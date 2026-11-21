import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

from fastapi_canon import __version__
from fastapi_canon.cli.application import open_application
from fastapi_canon.integrations.webrtc.codegen.typescript import (
    DEFAULT_CLIENT_NAME,
    ContractError,
    generate_typescript,
)
from fastapi_canon.integrations.webrtc.codegen.writer import write_files


def main(  # smelt: ignore[SMT429] -- fastapi_canon.commands entry point.
    argv: list[str] | None = None,
) -> int:
    """Export WebRTC contracts and generate browser clients."""
    parser = argparse.ArgumentParser(prog="fastapi-canon webrtc")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    contract = commands.add_parser(
        "contract", help="Export the session contract of a FastAPI application"
    )
    contract.add_argument("--app", required=True, help="module:attribute")
    contract.add_argument("--output", required=True, type=Path)
    contract.add_argument(
        "--check", action="store_true", help="Fail if the file is out of date"
    )

    generate = commands.add_parser(
        "generate", help="Generate the TypeScript browser client of a contract"
    )
    source = generate.add_mutually_exclusive_group(required=True)
    source.add_argument("--contract", type=Path, help="Contract JSON file")
    source.add_argument("--app", help="module:attribute, instead of --contract")
    generate.add_argument("--output", required=True, type=Path)
    generate.add_argument("--client-name", default=DEFAULT_CLIENT_NAME)
    generate.add_argument(
        "--check", action="store_true", help="Fail if generated files are out of date"
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "contract":
            return _contract(args.app, cast(Path, args.output), check=args.check)
        return _generate(args)
    except (
        ContractError,
        OSError,
        TypeError,
        ValueError,
        ImportError,
        AttributeError,
    ) as exc:
        print(f"fastapi-canon webrtc: {exc}", file=sys.stderr)
        return 2


def _contract(reference: str, output: Path, *, check: bool) -> int:
    from fastapi_canon.integrations.webrtc.contract import dumps

    content = dumps(_app_contract(reference))
    if check:
        if not output.is_file() or output.read_bytes() != content.encode("utf-8"):
            print(f"Out of date: {output}", file=sys.stderr)
            return 1
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")
    return 0


def _generate(args: argparse.Namespace) -> int:
    contract: Any
    if args.app is not None:
        contract = _app_contract(args.app)
    else:
        contract = json.loads(cast(Path, args.contract).read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ContractError("The contract must be a JSON object")
    encoded = json.dumps(
        contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    generation: dict[str, str | None] = {
        "generator": "fastapi-canon",
        "generator_version": __version__,
        "contract_sha256": hashlib.sha256(encoded).hexdigest(),
        "contract_hash_encoding": "canonical-json-v1",
        "target": "typescript",
        "client_name": args.client_name,
    }
    files = generate_typescript(contract, client_name=args.client_name)
    changed = write_files(
        cast(Path, args.output), files, check=args.check, generation=generation
    )
    if args.check and changed:
        for path in changed:
            print(f"Out of date: {path}", file=sys.stderr)
        return 1
    return 0


def _app_contract(reference: str) -> dict[str, Any]:
    from fastapi_canon.integrations.webrtc.contract import build_contract

    with open_application(reference, root=Path.cwd()) as app:
        return build_contract(app)


if __name__ == "__main__":
    sys.exit(main())
