"""Find missing route error contracts: ``python -m fastapi_canon.check_error_contracts``."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from fastapi_canon.checks.contracts import _check_error_contracts


def _main(
    arguments: Sequence[str] | None = None,
    *,
    prog: str = "python -m fastapi_canon.check_error_contracts",
) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Check explicit application exceptions against HTTP, SSE, WebRTC and RPC raises declarations without importing application code.",
        epilog="This is bounded source analysis, not proof of all runtime exception paths. "
        "Use --strict and --require-complete together to reject both contract gaps and incomplete analysis. "
        "Exit codes: 0 selected policy passes; 1 policy findings; 2 invalid roots, source or arguments.",
    )
    parser.add_argument(
        "roots",
        nargs="+",
        type=Path,
        help="Python import roots, e.g. src (not src/app)",
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
    try:
        report = _check_error_contracts(args.roots)
    except (OSError, SyntaxError, UnicodeError, ValueError) as error:
        parser.exit(2, f"{prog}: {error}\n")
    if args.format == "json":
        print(json.dumps(report.as_dict(), indent=2))
    else:
        for operation in report.operations:
            for diagnostic in operation.diagnostics:
                print(
                    f"{diagnostic.path}:{diagnostic.line}: {diagnostic.code} [{operation.function}] {diagnostic.message}"
                )
                if diagnostic.trace:
                    print(f"  {' -> '.join(diagnostic.trace)}")
        print(
            f"Analyzed {len(report.operations)} operations; {report.gaps} contract gaps; {report.incomplete} incomplete operations."
        )
        if not report.operations:
            print(
                "No supported Canon operations found; check the import roots and supported declaration forms."
            )
    return int(
        bool(
            (args.strict and report.gaps)
            or (args.require_complete and (report.incomplete or not report.operations))
        )
    )


if __name__ == "__main__":
    raise SystemExit(_main())
