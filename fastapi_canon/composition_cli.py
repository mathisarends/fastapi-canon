"""CLI adapters for imported composition checks and installed HTTP snapshots."""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI

from fastapi_canon.application_loading import _load_application, _load_reference
from fastapi_canon.diagnostics import CheckReport, CompositionInfo
from fastapi_canon.feature import Composition, inspect_app


def _main(arguments: Sequence[str], *, inspect: bool = False) -> int:
    command = "inspect" if inspect else "check composition"
    parser = argparse.ArgumentParser(
        prog=f"fastapi-canon {command}",
        description=(
            "Describe an installed HTTP composition from an application or synchronous app factory."
            if inspect
            else "Preflight a Composition against a fresh or configured FastAPI application without installing it."
        ),
        epilog=(
            "Imports and synchronous app factories execute application code; lifespan is never entered. "
            + (
                "Exit codes: 0 success; 2 invalid target or unavailable snapshot."
                if inspect
                else "Exit codes: 0 success; 1 findings at the selected threshold; 2 invalid target or incomplete report when required. "
                "Preflight is bounded validation, not proof of runtime exception coverage."
            )
        ),
    )
    parser.add_argument(
        "target",
        help=(
            "module:app or module:factory; dotted attributes are supported"
            if inspect
            else "module:composition resolving to a Composition instance; dotted attributes are supported"
        ),
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format: text (default) or a JSON snapshot/check report",
    )
    if inspect:
        parser.add_argument(
            "--factory",
            action="store_true",
            help="Compatibility flag: synchronous app factories are accepted automatically and called without arguments",
        )
    else:
        parser.add_argument(
            "--app",
            help="Check against module:app or a synchronous factory called without arguments (default: a fresh FastAPI app)",
        )
        parser.add_argument(
            "--require-complete",
            action="store_true",
            help="Exit 2 if a check is skipped or incomplete; does not execute unmodeled runtime behavior",
        )
        parser.add_argument(
            "--fail-on",
            choices=("error", "warning"),
            default="error",
            help="Finding threshold for exit 1: error (default), or warning to include warnings",
        )
    args = parser.parse_args(arguments)
    try:
        if inspect:
            target = _load_application(args.target, root=Path.cwd())
            result: CompositionInfo | CheckReport = inspect_app(target)
        else:
            target = _load_reference(args.target, root=Path.cwd())
            if not isinstance(target, Composition):
                raise ValueError("check target must be a Composition")
            app = (
                _load_application(args.app, root=Path.cwd()) if args.app else FastAPI()
            )
            result = target.check(app)
    except (ImportError, AttributeError, ValueError, TypeError) as error:
        if args.format == "json":
            print(json.dumps({"error": str(error)}))
        else:
            print(str(error), file=sys.stderr)
        return 2
    if args.format == "json":
        payload = asdict(result)
        payload["schema_version"] = 1
        if isinstance(result, CheckReport):
            payload.update(has_errors=result.has_errors, complete=result.complete)
        print(json.dumps(payload, indent=2))
    elif isinstance(result, CompositionInfo):
        for feature in result.features:
            print(f"Feature {feature.name}")
            for provider in feature.providers:
                print(f"  provider source: {provider}")
            for operation in feature.operations:
                print(
                    f"  {'|'.join(operation.methods)} {operation.path} [{operation.operation_id}]"
                )
                for dependency in operation.dependencies:
                    print(f"    dependency: {dependency.name} ({dependency.origin})")
                for problem in operation.errors:
                    print(
                        f"    error: {problem.code} {problem.status} ({problem.origin})"
                    )
    else:
        for issue in result.issues:
            print(f"{issue.code}: {issue.message}")
            print(f"  Features: {', '.join(issue.feature_names)}")
            for location in issue.locations:
                print(f"  {location}")
            print(f"  Hint: {issue.hint}")
        for check in result.checks:
            print(f"{check.name}: {check.status}")
    if isinstance(result, CheckReport):
        if args.require_complete and not result.complete:
            return 2
        return int(
            result.has_errors or (args.fail_on == "warning" and bool(result.issues))
        )
    return 0
