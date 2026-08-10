# Workspace

## When to use a workspace

Use a uv workspace when one repository contains two or more genuine Python packages that:

- have independent `pyproject.toml` files and import namespaces;
- are reused by multiple applications, workers, or tools;
- need editable local resolution while being developed together; or
- have different runtime dependency sets or build artifacts.

Keep one package when the proposed members are only architectural layers or feature slices of one service. A workspace boundary creates versioning, dependency, validation, and publishing overhead; it is not a substitute for Python package boundaries.

A typical service workspace is:

```text
repository/
├── pyproject.toml          # workspace, shared dev tools, shared constraints
├── uv.lock
├── compose.yml
├── services/
│   ├── api/
│   │   ├── pyproject.toml
│   │   └── src/api/
│   └── worker/
│       ├── pyproject.toml
│       └── src/worker/
└── packages/
    └── contracts/
        ├── pyproject.toml
        └── src/contracts/
```

The root declares membership and shared developer dependencies:

```toml
[project]
name = "product-workspace"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = []

[tool.uv.workspace]
members = ["services/*", "packages/*"]

[dependency-groups]
dev = ["mypy", "ruff"]
```

A member makes a local dependency explicit and asks uv to resolve it from the workspace:

```toml
[project]
name = "api"
dependencies = ["contracts"]

[tool.uv.sources]
contracts = { workspace = true }
```

Do not add a package to a member's dependencies only because the packages share a repository. Dependency declarations must reflect runtime imports. Avoid cyclic member dependencies.

## Workspace commands

Keep one lockfile at the root. Run cross-cutting verification from there:

```console
uv sync --locked --all-groups
uv run ruff check .
uv run mypy services packages
```

Document each member's runnable entry points. CI must verify the shared lock and dependency graph.

When workspace members run as containers, use the repository root as build context so the lockfile and local packages are available. See [Docker and Compose](docker.md) for general image and integration-topology rules.
