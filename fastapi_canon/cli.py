"""Canon commands with lazily loaded transport tools."""

import argparse
import shutil
import sys
from collections.abc import Sequence
from importlib.resources import as_file, files
from pathlib import Path

from fastapi_canon._version import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fastapi-canon")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("sse", "Export SSE contracts and generate Python or TypeScript clients"),
        ("websockets", "Export OpenRPC contracts and generate WebSocket clients"),
        ("webrtc", "Export WebRTC contracts and generate browser clients"),
        ("check", "Check source contracts or an imported application composition"),
        ("inspect", "Describe an installed application composition"),
        ("generate", "Generate one domain SDK from configured application contracts"),
        ("db", "Manage database revisions with Alembic"),
        ("skill", "Install the bundled backend-building skill in your project"),
    ):
        commands.add_parser(name, help=description, add_help=False)
    args, remaining = parser.parse_known_args(argv)
    if args.command == "inspect":
        from fastapi_canon.composition_cli import _main as inspect_main

        return inspect_main(remaining, inspect=True)
    elif args.command == "skill":
        return _skill_main(remaining)
    elif args.command == "db":
        from fastapi_canon.integrations.database.cli import main as database_main

        return database_main(remaining)
    elif args.command == "generate":
        from fastapi_canon.codegen.cli import main as generate_main

        return generate_main(remaining)
    elif args.command == "sse":
        from fastapi_canon.integrations.sse.codegen.cli import main as sse_main

        return sse_main(remaining)
    elif args.command == "websockets":
        from fastapi_canon.integrations.websockets.codegen.cli import (
            main as websockets_main,
        )

        return websockets_main(remaining)
    elif args.command == "webrtc":
        from fastapi_canon.integrations.webrtc.codegen.cli import main as webrtc_main

        return webrtc_main(remaining)
    else:
        if remaining and remaining[0] == "composition":
            from fastapi_canon.composition_cli import _main as composition_main

            return composition_main(remaining[1:])
        checker = argparse.ArgumentParser(prog="fastapi-canon check")
        checker.add_argument("kind", choices=("errors", "composition"))
        _, remaining = checker.parse_known_args(remaining)
        from fastapi_canon.check_error_contracts import _main as check_main

        return check_main(remaining)


def _skill_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="fastapi-canon skill")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="Copy the bundled skill")
    install.add_argument(
        "--destination",
        type=Path,
        default=Path(".agents/skills/build-canon-backend"),
        help="Skill directory to create (must not exist)",
    )
    args = parser.parse_args(argv)
    source = files("fastapi_canon").joinpath("skills", "build-canon-backend")
    try:
        with as_file(source) as source_path:
            shutil.copytree(source_path, args.destination)
    except OSError as exc:
        print(f"Could not install skill: {exc}", file=sys.stderr)
        return 2
    print(f"Installed build-canon-backend in {args.destination.resolve()}")
    return 0
