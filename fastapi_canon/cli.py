"""Canon commands with lazily loaded transport tools."""

import argparse
import shutil
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from importlib.resources import as_file, files
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi_canon._version import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fastapi-canon",
        description="Declare and check Canon applications, export contracts and generate domain SDKs.",
        epilog="Use COMMAND --help for target syntax, defaults, side effects and exit policies.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("contracts", "Export contracts from the registered application routes"),
        ("sse", "Export SSE contracts and generate Python or TypeScript clients"),
        ("websockets", "Export OpenRPC contracts and generate WebSocket clients"),
        ("webrtc", "Export WebRTC contracts and generate browser clients"),
        ("check", "Check source contracts or an imported application composition"),
        ("inspect", "Describe an installed application composition"),
        ("explain", "Explain a built-in source, composition or generation diagnostic"),
        ("generate", "Generate one domain SDK from configured application contracts"),
        ("db", "Manage database revisions with Alembic"),
        ("skill", "Install the bundled backend-building skill in your project"),
    ):
        commands.add_parser(name, help=description, add_help=False)
    args, remaining = parser.parse_known_args(argv)
    if args.command == "contracts":
        from fastapi_canon.contract_cli import main as contracts_main

        return contracts_main(remaining)
    elif args.command == "inspect":
        from fastapi_canon.composition_cli import _main as inspect_main

        return inspect_main(remaining, inspect=True)
    elif args.command == "explain":
        from fastapi_canon.diagnostic_explanations import _main as explain_main

        return explain_main(remaining)
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
        return _check_main(remaining)


def _check_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="fastapi-canon check",
        description="Source error checking never imports application code; composition checking imports its target modules.",
    )
    commands = parser.add_subparsers(dest="kind", required=True)
    commands.add_parser("errors", help="Check source error contracts", add_help=False)
    commands.add_parser(
        "composition", help="Check an application composition", add_help=False
    )
    args, remaining = parser.parse_known_args(argv)
    if args.kind == "composition":
        from fastapi_canon.composition_cli import _main as composition_main

        return composition_main(remaining)
    from fastapi_canon.check_error_contracts import _main as check_main

    return check_main(remaining, prog="fastapi-canon check errors")


def _skill_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="fastapi-canon skill")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="Copy the bundled skill")
    install.add_argument(
        "--destination",
        type=Path,
        help="Install only into this skill directory instead of .agents and .claude",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="Replace existing skill directories, including obsolete resources",
    )
    args = parser.parse_args(argv)
    destinations = (
        [args.destination]
        if args.destination is not None
        else [
            Path(".agents/skills/build-canon-backend"),
            Path(".claude/skills/build-canon-backend"),
        ]
    )
    source = files("fastapi_canon").joinpath("skills", "build-canon-backend")
    try:
        with as_file(source) as source_path:
            _install_skill(source_path, destinations, force=args.force)
    except OSError as exc:
        print(f"Could not install skill: {exc}", file=sys.stderr)
        return 2
    for destination in destinations:
        print(
            f"Installed build-canon-backend (Canon {__version__}) "
            f"in {destination.resolve()}"
        )
    return 0


def _install_skill(source: Path, destinations: Sequence[Path], *, force: bool) -> None:
    resolved = [destination.resolve() for destination in destinations]
    if len(set(resolved)) != len(resolved):
        raise OSError("Skill destinations must be distinct directories")
    current = Path.cwd().resolve()
    for destination, target in zip(destinations, resolved, strict=True):
        if destination.is_symlink() or destination.is_junction():
            raise OSError(f"Skill destination must not be a link: {destination}")
        if current.is_relative_to(target) or source.resolve().is_relative_to(target):
            raise OSError(f"Skill destination contains the project or bundle: {target}")
        if destination.exists() and (not force or not destination.is_dir()):
            raise FileExistsError(
                f"{destination} already exists; use --force to replace a skill directory"
            )

    # Stage every copy before replacing any destination. Keep the previous trees
    # until all replacements succeed so an installation error can restore them.
    with ExitStack() as stack:
        prepared: list[tuple[Path, Path, Path]] = []
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(
                stack.enter_context(
                    TemporaryDirectory(prefix=".canon-skill-", dir=destination.parent)
                )
            )
            staged, backup = temporary / "skill", temporary / "previous"
            shutil.copytree(source, staged)
            prepared.append((destination, staged, backup))
        try:
            for destination, staged, backup in prepared:
                if destination.exists():
                    destination.rename(backup)
                staged.rename(destination)
        except OSError:
            for destination, staged, backup in reversed(prepared):
                if not staged.exists():
                    destination.rename(staged)
                if backup.exists():
                    backup.rename(destination)
            raise
