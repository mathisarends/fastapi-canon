# Contributing

Use Python 3.12–3.14, uv 0.9.2 or newer, Node.js 24 and pnpm 12.14.0.
Run commands from the repository root.

## Set up

```console
uv sync --frozen --all-groups
pnpm install --frozen-lockfile --ignore-scripts
uv run --frozen smelt context <module-or-path>
```

Read [architecture](architecture.md) before changing boundaries or test layout.
Use English for repository Markdown and `.yml` for repository-owned YAML,
preserving tool-defined names such as `pnpm-lock.yaml`.

## Check changes

```console
uv run --frozen smelt check --config smelt.yml --changed --fail-on warning --format json
uv run --frozen python scripts/check.py all
uv run --frozen pre-commit run --config .pre-commit-config.yml --all-files
```

The `all` gate runs architecture, lint/format, types, Python tests, client drift
and TypeScript runtime/coverage checks. Each gate can also run separately:
`architecture`, `lint`, `types`, `test` or `clients`.
Warnings fail the [architecture policy](architecture-guardrails.md).
Inspect hook fixes and rerun affected checks.

All library Python tests share one root invocation. Select paths for focused work:

```console
uv run --frozen pytest tests/unit/error tests/integration/error tests/contract/error
```

The test gate also runs the reference service's application suite. Regenerate
committed clients through their generators before checking drift.

## Verify release artifacts

```console
uv run --locked python scripts/check_distributions.py
uv run --locked python scripts/check_distributions.py --python 3.12 --dependencies minimum
uv run --frozen python scripts/check_telemetry_compatibility.py --python 3.12
uv run --locked python scripts/check_streams_artifacts.py
uv run --locked python scripts/check_webrtc_artifacts.py
```

Distribution checks install wheels in fresh environments with current and minimum
dependencies. Installed CLI checks generate and validate real clients.
Reports and dependency inventories go in ignored `dist/`.

The browser gate runs separately from `all`:

```console
pnpm exec playwright install chromium
uv run --frozen python scripts/check.py browser
```

It exercises Chromium against aiortc. Live PostgreSQL contracts run in the CI
database job. See [compatibility](compatibility.md) for tested scope and
[releases](releases.md) for publication requirements.

For applications to run locally, browse the [examples](../examples/README.md).
