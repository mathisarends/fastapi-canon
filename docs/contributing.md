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

## Choose tests

Add a test when behavior changes or an existing guarantee lacks coverage. Start
with one regression for a bug, or a success case and distinct failure/boundary
cases for new behavior. There is no required number of tests per function or PR.
Type suppressions must name the diagnostic code and explain the concrete reason
on the same line (`# type: ignore[code]  # Reason.`). Prefer a typed adapter or cast
when it expresses the boundary. Ruff rejects blanket type ignores and private
symbol imports (`PLC2701`, enabled explicitly as a preview rule). Shared
implementation contracts have descriptive
names in their owning modules; supported consumer imports remain the documented
package facades. Keep private helpers local to their module. WebSockets is
checked by root strict mypy; installed-wheel API completeness is an additional
consumer check. SSE and WebRTC keep their scoped type profiles.

Pure documentation, formatting and behavior-preserving refactors normally use
the existing checks.

Choose the smallest public boundary that exposes the failure. Unit tests cover
isolated behavior; integration tests cover collaboration and resource lifetimes;
contract tests cover consumer-visible schemas and APIs. Add a second level only
when it establishes a different guarantee. Use real collaborators in integration
and contract tests and at most one object-targeted patch in a unit test.

Parameterize distinct equivalence classes rather than every possible combination.
Prefer importing, compiling and running generated clients to checking incidental
source strings. Keep exact checks for public names, wire/schema contracts,
ownership and drift. Remove or merge tests only after identifying where their
guarantees remain covered; test count alone is not a reason for deletion.

## Measure check and test times

Record command timings and Python test reports without changing the selected gates:

```console
uv run --frozen python scripts/check.py all --report-dir dist/checks
uv run --frozen python scripts/benchmark_tests.py --rounds 3 --output dist/benchmarks/before.json -- --cov=fastapi_canon.integrations.webrtc --cov-branch --cov-fail-under=85
```

The benchmark runs the real suite in a fresh Python process per round. Its JSON
records environment and lockfile identity, outcomes, collection time, test phases,
fixture setup and direct subprocess starts. Extra pytest arguments follow `--`; use the
same selection, coverage options, environment and cache conditions before and
after an optimization. Measure focused candidates first, then the complete suite.
Initial dependency installation is outside the benchmark. Compare warm runs
separately from the first run and inspect medians, not a single fastest result.

Reuse immutable generated or compiled artifacts when setup dominates. Keep
mutable application state isolated per test. Distinguish reduced runner time
from the CI's critical path; independent jobs overlap. Reports stay in ignored
`dist/`, and CI attaches command timings and JUnit reports to each run.

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
