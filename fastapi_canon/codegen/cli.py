import argparse
import io
import json
import sys
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from pathlib import Path

from fastapi_canon.codegen.config import load_config
from fastapi_canon.codegen.generate import generate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fastapi-canon generate",
        description="Export contracts once and run specialized or external client generators.",
        epilog="Live sources import application code; lifespan is never entered. Exit codes: 0 success; 1 drift; 2 invalid configuration or generator error. Check/diff never writes Canon-owned files. External check commands must be read-only.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Version-2 configuration (default: fastapi-canon.toml, otherwise pyproject.toml)",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Select a target; repeat for several targets (default: all targets)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Read saved contracts without importing application code",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check contracts and clients without writing",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show native artifact differences; implies --check",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Text (default) summary or versioned JSON report",
    )
    args = parser.parse_args(argv)
    captured = io.StringIO()
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            path = args.config or (
                Path("fastapi-canon.toml")
                if Path("fastapi-canon.toml").exists()
                else Path("pyproject.toml")
            )
            config = load_config(path)
            results = generate(
                config,
                targets=tuple(args.target),
                offline=args.offline,
                check=args.check,
                diff=args.diff,
            )
    except Exception as error:
        message = (
            "Install fastapi-canon[codegen] to generate specialized clients"
            if isinstance(error, ImportError)
            else str(error)
            if isinstance(error, (ValueError, TypeError, OSError))
            else f"Generation failed ({type(error).__name__}); check configuration and application export"
        )
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "schema_version": 2,
                        "status": "error",
                        "error": message,
                        "check": args.check or args.diff,
                        "offline": args.offline,
                        "diagnostics": captured.getvalue(),
                        "steps": [],
                    }
                )
            )
        else:
            print(f"fastapi-canon generate: {message}", file=sys.stderr)
        return 2
    code = (
        2
        if any(item.status == "error" for item in results)
        else int(any(item.status in {"drift", "blocked"} for item in results))
    )
    if args.format == "json":
        print(
            json.dumps(
                {
                    "schema_version": 2,
                    "status": "error"
                    if code == 2
                    else "drift"
                    if code == 1
                    else "success",
                    "check": args.check or args.diff,
                    "offline": args.offline,
                    "steps": [asdict(item) for item in results],
                    "diagnostics": captured.getvalue(),
                },
                indent=2,
            )
        )
    else:
        if captured.getvalue():
            print(captured.getvalue(), end="", file=sys.stderr)
        for item in results:
            print(f"{item.name}: {item.status}")
            for changed in item.changes:
                print(f"  {changed}")
            if item.diff:
                print(item.diff, end="")
            if item.output:
                print(item.output, end="" if item.output.endswith("\n") else "\n")
    return code
