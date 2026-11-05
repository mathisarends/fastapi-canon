from __future__ import annotations

import argparse
import io
import json
import sys
from collections.abc import Sequence
from contextlib import redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi_canon.codegen.config import load_config
from fastapi_canon.codegen.ir import GenerationError

if TYPE_CHECKING:
    from fastapi_canon.codegen.generate import _GenerationResult


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fastapi-canon generate")
    parser.add_argument(
        "--config",
        type=Path,
        help="Configuration path (default: fastapi-canon.toml, then pyproject.toml)",
    )
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Diagnostic output format; JSON uses a versioned envelope on stdout",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Read saved contracts without importing application code",
    )
    parser.add_argument(
        "--check", action="store_true", help="Check for drift without writing"
    )
    parser.add_argument(
        "--diff", action="store_true", help="Show the proposed diff; implies --check"
    )
    args = parser.parse_args(argv)
    checked = args.check or args.diff
    selected = tuple(args.target)
    try:
        from fastapi_canon.codegen.generate import _generate, generate

        path = args.config or (
            Path("fastapi-canon.toml")
            if Path("fastapi-canon.toml").exists()
            else Path("pyproject.toml")
        )
        configuration = load_config(path)
        selected = tuple(
            target.name
            for target in configuration.targets
            if not args.target or target.name in args.target
        ) or tuple(args.target)
        if args.format == "text":
            changed, report = generate(
                configuration,
                targets=tuple(args.target),
                offline=args.offline,
                check=checked,
                diff=args.diff,
            )
            if report:
                print(report)
            return int(changed and checked)
        # Application imports/factories may print. JSON stdout belongs entirely
        # to the diagnostic envelope, including on a failed export.
        with redirect_stdout(io.StringIO()):
            result = _generate(
                configuration,
                targets=tuple(args.target),
                offline=args.offline,
                check=checked,
                diff=args.diff,
                collect_diagnostics=True,
            )
    except Exception as exc:
        if args.format == "json":
            print(
                _json_report(
                    result=None,
                    targets=selected,
                    check=checked,
                    offline=args.offline,
                    diff=args.diff,
                    error=exc,
                )
            )
        else:
            print(f"fastapi-canon generate: {_failure_message(exc)}", file=sys.stderr)
        return 2
    print(
        _json_report(
            result=result,
            targets=result.targets,
            check=checked,
            offline=args.offline,
            diff=args.diff,
        )
    )
    return int(result.changed and checked)


def _json_report(
    *,
    result: _GenerationResult | None,
    targets: tuple[str, ...],
    check: bool,
    offline: bool,
    diff: bool,
    error: Exception | None = None,
) -> str:
    diagnostics: list[dict[str, Any]] = []
    if result is not None:
        diagnostics.extend(
            {
                "severity": "warning",
                "code": "canon.codegen.W002",
                "message": item.message,
                "locations": [
                    {
                        "source": item.source,
                        "operation": item.operation,
                        "path": item.path,
                        "target": item.target,
                        "schema_pointer": item.schema_pointer,
                    }
                ],
            }
            for item in result.diagnostics
        )
    if error is not None:
        locations = []
        if isinstance(error, GenerationError):
            location = {
                key: value
                for key, value in {
                    "source": error.source,
                    "operation": error.operation,
                    "path": error.path,
                    "target": error.target,
                }.items()
                if value is not None
            }
            if location:
                locations.append(location)
        diagnostics.append(
            {
                "severity": "error",
                "code": error.code
                if isinstance(error, GenerationError)
                else "canon.codegen.E001",
                "message": _failure_message(error),
                "locations": locations,
            }
        )
    elif result is not None and result.changed:
        diagnostics.append(
            {
                "severity": "warning" if check else "info",
                "code": "canon.codegen.W001" if check else "canon.codegen.I001",
                "message": "Generated artifacts are out of date"
                if check
                else "Generated artifacts updated",
                "locations": [{"path": path} for path in result.paths],
            }
        )
    return json.dumps(
        {
            "schema_version": 1,
            "command": "generate",
            "status": "error"
            if error is not None
            else "drift"
            if result and result.changed and check
            else "success",
            "changed": result.changed if result is not None else None,
            "check": check,
            "offline": offline,
            "targets": list(targets),
            "changes": list(result.paths) if result is not None else [],
            "diagnostics": diagnostics,
            "diff": result.report if result is not None and diff else None,
        },
        indent=2,
    )


def _failure_message(error: Exception) -> str:
    if isinstance(error, GenerationError):
        return str(error)
    if isinstance(error, ImportError):
        return "Generation dependencies are unavailable; install fastapi-canon[codegen]"
    return f"Generation failed ({type(error).__name__}); check the configuration and application export"
