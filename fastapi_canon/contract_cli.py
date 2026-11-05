"""Command-line export of application contracts."""

import argparse
import difflib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from fastapi_canon.application_loading import _load_application
from fastapi_canon.codegen.ir import ContractKind
from fastapi_canon.contracts import export_contracts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fastapi-canon contracts")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser(
        "export", help="Export registered application contracts"
    )
    export.add_argument(
        "--app",
        required=True,
        help="module:attribute of an app or zero-argument app factory",
    )
    export.add_argument("--output-dir", required=True, type=Path)
    export.add_argument(
        "--kind", choices=("openapi", "openrpc", "webrtc"), action="append"
    )
    export.add_argument(
        "--base-url", default="{baseUrl}", help="Public RPC origin or template"
    )
    export.add_argument(
        "--check", action="store_true", help="Check drift without writing"
    )
    export.add_argument(
        "--diff", action="store_true", help="Show drift; implies --check"
    )
    args = parser.parse_args(argv)
    try:
        app = _load_application(args.app, root=Path.cwd())
        kinds: tuple[ContractKind, ...] = tuple(
            args.kind or ("openapi", "openrpc", "webrtc")
        )
        contracts = export_contracts(app, kinds=kinds, base_url=args.base_url)
        if contracts.openapi is not None:
            from fastapi_canon.integrations.sse.codegen.openapi import validate_contract

            validate_contract(contracts.openapi)
        missing = set(args.kind or ()) - contracts.documents.keys()
        if missing:
            raise ValueError(
                "No registered endpoints for: " + ", ".join(sorted(missing))
            )
        # Complete export and serialization before writing any document.
        outputs = {
            args.output_dir / f"{kind}.json": json.dumps(
                document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
            )
            + "\n"
            for kind, document in contracts.documents.items()
        }
        # The selected stable filenames belong to this export directory. Removing
        # a transport must not leave a stale contract available to offline SDKs.
        for kind in set(kinds) - contracts.documents.keys():
            path = args.output_dir / f"{kind}.json"
            if path.exists():
                outputs[path] = ""
        for path in outputs:
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                raise ValueError(f"Contract output must not traverse symlinks: {path}")
            if path.exists() and not path.is_file():
                raise ValueError(f"Contract output is not a regular file: {path}")
        changed = False
        for path, content in outputs.items():
            previous = path.read_text(encoding="utf-8") if path.exists() else ""
            if path.exists() and previous == content:
                continue
            changed = True
            if args.check or args.diff:
                print(f"Out of date: {path}")
                if args.diff:
                    print(
                        "".join(
                            difflib.unified_diff(
                                previous.splitlines(True),
                                content.splitlines(True),
                                fromfile=str(path),
                                tofile=str(path),
                            )
                        ),
                        end="",
                    )
            else:
                if content:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8", newline="\n")
                else:
                    path.unlink()
        return int(changed and (args.check or args.diff))
    except Exception as exc:
        print(f"fastapi-canon contracts export: {exc}", file=sys.stderr)
        return 2
