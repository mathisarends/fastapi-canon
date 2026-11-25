import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi_canon.checks.configuration import load_applications
from fastapi_canon.checks.contracts import ContractReport, analyze_error_contracts

_ROOT_CAUSES = 10
_ANALYSIS_ERRORS = (OSError, SyntaxError, UnicodeError, ValueError)


def main(
    arguments: Sequence[str] | None = None,
    *,
    prog: str = "python -m fastapi_canon.checks",
) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Check explicit application exceptions against HTTP, SSE, WebRTC and RPC raises declarations without importing application code.",
        epilog="This is bounded source analysis, not proof of all runtime exception paths. "
        "Use --strict and --require-complete together to reject both contract gaps and incomplete analysis. "
        "Exit codes: 0 selected policy passes; 1 policy findings; 2 invalid roots, configuration, source or arguments. "
        "With several --config files, the highest status of any application.",
    )
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Python import roots, e.g. src (not src/app); not combined with --config",
    )
    parser.add_argument(
        "--reference-root",
        action="append",
        default=[],
        type=Path,
        dest="references",
        help="Import root whose definitions resolve without contributing operations, catalog entries or provider bindings; repeatable",
    )
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        type=Path,
        dest="configs",
        help="fastapi-canon.toml or pyproject.toml with [checks.errors]; one application each, analyzed separately; repeatable",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 for CAN001/CAN002 missing mappings or declarations (default: report gaps without failing)",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit 1 for CAN003 incomplete analysis or no supported operations; independent of --strict",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format: text (default) or a JSON source-analysis report",
    )
    args = parser.parse_args(arguments)
    if args.configs and (args.roots or args.references):
        parser.exit(
            2, f"{prog}: --config cannot be combined with roots or --reference-root\n"
        )
    if not args.configs and not args.roots:
        parser.error("supply import roots or --config")
    if not args.configs:
        try:
            report = analyze_error_contracts(args.roots, args.references)
        except _ANALYSIS_ERRORS as error:
            parser.exit(2, f"{prog}: {error}\n")
        if args.format == "json":
            print(json.dumps(report.as_dict(), indent=2))
        else:
            _print(report)
        return _status(report, strict=args.strict, complete=args.require_complete)
    try:
        applications = load_applications(args.configs)
        for application in applications:
            if application.errors is None:
                raise ValueError(f"{application.path}: missing [checks.errors]")
    except (OSError, ValueError) as error:
        parser.exit(2, f"{prog}: {error}\n")
    status = 0
    results: list[dict[str, Any]] = []
    for application in applications:
        settings = application.errors
        assert settings is not None
        if args.format == "text" and len(applications) > 1:
            print(f"== {application.name} ({application.path})")
        try:
            report = analyze_error_contracts(list(settings.roots), settings.references)
        except _ANALYSIS_ERRORS as error:
            status = 2
            results.append({"name": application.name, "report": {"error": str(error)}})
            if args.format == "text":
                print(f"{prog}: {error}", file=sys.stderr)
            continue
        status = max(
            status,
            _status(
                report,
                strict=args.strict or settings.strict,
                complete=args.require_complete or settings.require_complete,
            ),
        )
        results.append({"name": application.name, "report": report.as_dict()})
        if args.format == "text":
            _print(report)
    if args.format == "json":
        if len(results) == 1:
            print(
                json.dumps(
                    {"name": results[0]["name"], **results[0]["report"]}, indent=2
                )
            )
        else:
            print(json.dumps({"schema_version": 1, "applications": results}, indent=2))
    return status


def _status(report: ContractReport, *, strict: bool, complete: bool) -> int:
    return int(
        bool(
            (strict and report.gaps)
            or (complete and (report.incomplete or not report.operations))
        )
    )


def _print(report: ContractReport) -> None:
    for operation in report.operations:
        for diagnostic in operation.diagnostics:
            print(
                f"{diagnostic.path}:{diagnostic.line}: {diagnostic.code} [{operation.function}] {diagnostic.message}"
            )
            if diagnostic.trace:
                print(f"  {' -> '.join(diagnostic.trace)}")
    causes = report.root_causes
    if causes:
        print("Root causes of incomplete analysis:")
    for cause in causes[:_ROOT_CAUSES]:
        print(
            f"  {cause.path}:{cause.line}: {cause.message} ({cause.operations} operations)"
        )
    if len(causes) > _ROOT_CAUSES:
        print(f"  ... and {len(causes) - _ROOT_CAUSES} more; use --format json")
    print(
        f"Analyzed {len(report.operations)} operations; {report.gaps} contract gaps; {report.incomplete} incomplete operations."
    )
    if not report.operations:
        print(
            "No supported Canon operations found; check the import roots and supported declaration forms."
        )


if __name__ == "__main__":
    raise SystemExit(main())
