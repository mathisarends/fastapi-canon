"""Compile the combined SDK consumer for Node using the root TypeScript tools."""

import argparse
import shutil
import subprocess
from pathlib import Path


def _main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path, default=root / "examples/canonical_errors/dist/consumer"
    )
    arguments = parser.parse_args()
    node = shutil.which("node")
    compiler = root / "node_modules/typescript/bin/tsc"
    if node is None or not compiler.exists():
        parser.error("Run pnpm install --frozen-lockfile --ignore-scripts first")
    output = arguments.out_dir.resolve()
    subprocess.run(
        [
            node,
            str(compiler),
            "--project",
            str(root / "examples/canonical_errors/tsconfig.json"),
            "--outDir",
            str(output),
        ],
        cwd=root,
        check=True,
        timeout=60,
    )
    # Scope the emitted CommonJS files inside this repository's ESM tooling root.
    (output / "package.json").write_text('{"type":"commonjs"}\n', encoding="utf-8")


if __name__ == "__main__":
    _main()
