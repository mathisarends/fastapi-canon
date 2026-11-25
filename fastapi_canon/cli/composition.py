import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from fastapi_canon.checks.configuration import load_applications
from fastapi_canon.cli.application import load_reference, open_application
from fastapi_canon.composition import Composition, check_app, inspect_app
from fastapi_canon.composition.installation import close_installed_app
from fastapi_canon.diagnostics import CheckReport, CompositionInfo


def main(arguments: Sequence[str], *, inspect: bool = False) -> int:
    command = "inspect" if inspect else "check composition"
    parser = argparse.ArgumentParser(
        prog=f"fastapi-canon {command}",
        description=(
            "Describe an installed HTTP composition from an application or synchronous app factory."
            if inspect
            else "Preflight a Composition or check the composition installed on --app."
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
        nargs=None if inspect else "?",
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
        parser.add_argument(
            "--config",
            action="append",
            default=[],
            type=Path,
            dest="configs",
            help="fastapi-canon.toml or pyproject.toml naming the app in [checks.composition] or [codegen]; "
            "one application each, checked in its own process when repeated; not combined with a target or --app",
        )
    args = parser.parse_args(arguments)
    if not inspect and args.configs:
        if args.target is not None or args.app:
            return _fail(
                args.format, "--config cannot be combined with a target or --app"
            )
        return _check_configured(args)
    return _run(args, inspect=inspect, root=Path.cwd())


def _check_configured(args: argparse.Namespace) -> int:
    try:
        applications = load_applications(args.configs)
        for item in applications:
            if item.composition is None:
                raise ValueError(
                    f"{item.path}: name the app in [checks.composition] or [codegen]"
                )
    except (OSError, ValueError) as error:
        return _fail(args.format, str(error))
    if len(applications) == 1:
        (application,) = applications
        settings = application.composition
        assert settings is not None
        args.app = settings.app
        args.require_complete = args.require_complete or settings.require_complete
        if settings.fail_on == "warning":
            args.fail_on = "warning"
        return _run(
            args, inspect=False, root=application.path.parent, name=application.name
        )
    # Application imports and factories have process-wide effects; isolate each.
    status = 0
    reports: list[dict[str, Any]] = []
    for application in applications:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "fastapi_canon",
                "check",
                "composition",
                "--config",
                str(application.path),
                "--format",
                args.format,
                "--fail-on",
                args.fail_on,
                *(["--require-complete"] if args.require_complete else []),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        status = max(status, result.returncode)
        print(result.stderr, file=sys.stderr, end="")
        if args.format == "text":
            print(f"== {application.name} ({application.path})")
            print(result.stdout, end="")
            continue
        try:
            report = json.loads(result.stdout)
        except ValueError:
            status = 2
            report = {"error": "missing JSON report"}
        report.pop("name", None)
        report.pop("schema_version", None)
        reports.append({"name": application.name, "report": report})
    if args.format == "json":
        print(json.dumps({"schema_version": 1, "applications": reports}, indent=2))
    return status


def _run(
    args: argparse.Namespace, *, inspect: bool, root: Path, name: str | None = None
) -> int:
    result: CompositionInfo | CheckReport
    try:
        if inspect:
            with open_application(args.target, root=root) as app:
                result = inspect_app(app)
        elif args.target is not None or args.app:
            target = None
            if args.target is not None:
                target = load_reference(args.target, root=root)
                if not isinstance(target, Composition):
                    raise ValueError("check target must be a Composition")
            application = (
                open_application(args.app, root=root, close_factory=close_installed_app)
                if args.app
                else nullcontext(FastAPI())
            )
            with application as app:
                result = check_app(app) if target is None else target.check(app)
        else:
            raise ValueError("Supply a composition target or --app module:app")
    except (ImportError, AttributeError, ValueError, TypeError) as error:
        return _fail(args.format, str(error))
    if args.format == "json":
        payload: dict[str, Any] = {} if name is None else {"name": name}
        payload.update(asdict(result))
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


def _fail(output: str, message: str) -> int:
    if output == "json":
        print(json.dumps({"error": message}))
    else:
        print(message, file=sys.stderr)
    return 2
