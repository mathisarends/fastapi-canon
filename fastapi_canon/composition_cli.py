"""CLI adapters for imported composition checks and installed HTTP snapshots."""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from importlib import import_module
from typing import Any

from fastapi import FastAPI

from fastapi_canon.diagnostics import CheckReport, CompositionInfo
from fastapi_canon.feature import Composition, _installed_description


def _main(arguments: Sequence[str], *, inspect: bool = False) -> int:
    command = "inspect" if inspect else "check composition"
    parser = argparse.ArgumentParser(prog=f"fastapi-canon {command}")
    parser.add_argument(
        "target", help="module:app" if inspect else "module:composition"
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    if inspect:
        parser.add_argument(
            "--factory",
            action="store_true",
            help="Call the target as an application factory (without entering its lifespan)",
        )
    else:
        parser.add_argument(
            "--app", help="Check against this module:app instead of a fresh FastAPI app"
        )
        parser.add_argument("--require-complete", action="store_true")
        parser.add_argument("--fail-on", choices=("error", "warning"), default="error")
    args = parser.parse_args(arguments)
    try:
        target = _load(args.target)
        if inspect:
            if args.factory:
                target = target()
            if not isinstance(target, FastAPI):
                raise ValueError("inspect target must be a FastAPI application")
            result: CompositionInfo | CheckReport = _installed_description(target)
        else:
            if not isinstance(target, Composition):
                raise ValueError("check target must be a Composition")
            app = _load(args.app) if args.app else FastAPI()
            if not isinstance(app, FastAPI):
                raise ValueError("--app target must be a FastAPI application")
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


def _load(reference: str) -> Any:
    module, separator, name = reference.partition(":")
    if not separator or not module or not name:
        raise ValueError("Use an explicit module:attribute target")
    value: Any = import_module(module)
    for part in name.split("."):
        value = getattr(value, part)
    return value
