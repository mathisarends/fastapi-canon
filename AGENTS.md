# Working on fastapi-canon

Read `smelt.yml` and `docs/architecture.md` before changing package boundaries or
test structure. They describe this checkout's implemented library. Verify an API exists in this checkout before using it. Write repository Markdown in English.

Use `README.md` for the library overview, `docs/canon.md` for public namespaces,
`docs/contributing.md` for commands and `docs/compatibility.md` for tested scope.
Shared errors and common generation are documented in `docs/problems.md` and
`docs/codegen.md`. The canonical repository is
`mathisarends/fastapi-canon`; preserve historical release evidence in
`mathisarends/canon` as described in `docs/repository-migration.md`.

## Layout and boundaries

- This is one root package: `pyproject.toml` builds `fastapi_canon/`. Do not add a
  coordination project, `packages/` directory or Python/pnpm package workspace.
  Keep one root `uv.lock`, `package.json` and `pnpm-lock.yaml` for development.
- Core composition and HTTP contracts stay in `fastapi_canon`. Optional SSE,
  WebSockets, WebRTC and telemetry stay under `fastapi_canon.integrations`.
  WebSocket framework adapters live in `.websockets.adapters`. Core imports and
  CLI help must work without codegen, telemetry runtimes or media engines.
- Keep value objects, metadata and rendering independent of FastAPI, Starlette
  and Dishka. HTTP/OpenAPI adapters consume the read-only `ErrorCatalog` protocol,
  without importing the concrete registry. Routing depends on adapters;
  composition owns routing, Dishka and lifecycle integration.
- Transport modules must not import each other or load optional engines eagerly.
  Use one Canon version. Add shared abstractions only for concrete reuse.
- Consumer imports use `fastapi_canon`, `fastapi_canon.error` or the documented
  integration facades. Inside the library use absolute imports and the
  existing facade when it does not reverse dependency direction. Package facades
  retain their relative re-exports, using relative imports.
- Use descriptive module names. Keep private helpers inside those modules, with
  public definitions before private implementation details. Preserve Python 3.12.
- Tests mirror source under `tests/unit`, `tests/integration` and `tests/contract`.
  Topic suffixes are supported; package tests use `test_<package>.py`. Protocol
  scenario exceptions are explicit in Smelt. Shared snapshots belong under
  `tests/fixtures`; TypeScript and real browser checks have their own test roots.
- Integration and contract tests use real collaborators; mocks are forbidden.
  Unit tests may use at most one object-targeted patch. Test observable outcomes
  with finite local deadlines; do not access private production members to pass.
- Add tests for changed behavior or a demonstrated coverage gap, not for every
  edit or function. A bug normally needs one focused regression that fails before
  the fix. New behavior needs a success case and its distinct failure/boundary
  cases; there is no test-count quota. Documentation, formatting and behavior-
  preserving refactors normally use existing checks.
- Choose the smallest public boundary that can expose the regression. Add an
  integration or contract case when it establishes a different cross-component
  or consumer guarantee; do not repeat every case at all three levels.
  Parameterize meaningful equivalence classes, not arbitrary Cartesian products.
  Before adding a case, identify the regression an existing test cannot detect.
- Prefer generated-client import, compilation and execution over assertions about
  incidental emitted source text. Retain exact output checks for intentional
  public names, wire/schema contracts, ownership manifests and drift.
- Remove or merge a test only when its guarantee is retired or demonstrably
  covered elsewhere. Keep distinct cleanup, cancellation, compatibility and
  security outcomes. Profile before optimizing: reuse immutable generation and
  compilation fixtures, but keep application state fresh for each test.
- Put source/style policies in Smelt or Ruff, not AST-based pytest tests. Do not
  weaken rules, add broad exclusions or record a debt baseline to pass checks.
  Necessary inline exceptions need a concrete reason; unused suppressions fail.
- Runnable demos live in `examples/`, transport guides in `docs/integrations/`,
  development scripts in `scripts/` and scoped analyzer settings alongside the code they check.
  The reference service is an editable example dependency, not a release target.
  Application code uses feature slices, inward dependencies, framework-free domain
  and Dishka for application dependencies.

## Documentation

- Write for people who need to choose, install or use Canon. Lead with the task,
  show one small working example, then explain only the behavior needed to use it.
- Keep the README to an overview, installation and one example. Put walkthroughs
  in task guides and link to them instead of repeating their content.
- Document public contracts, ownership, limitations and migration steps. Read
  implementation details from code and executable policy; do not narrate modules,
  internal call graphs, test inventories or completed development work in guides.
- Give each topic one primary page. Reuse links, remove stale claims and verify
  examples against this checkout. Clearly label unpublished APIs and versions.
- Keep release records compact: version, status and links to source, CI, runs and
  artifacts. Preserve historical evidence; logs and manifests belong in Actions
  and GitHub Releases, not prose transcripts in the documentation.
- Update the affected guide when behavior changes. Avoid adding a design record,
  checklist or summary page unless it answers a distinct reader need.

## Workflow and verification

Run from the root:

```console
uv sync --frozen --all-groups
pnpm install --frozen-lockfile --ignore-scripts
uv run --frozen smelt context <module-or-path>
```

After editing, run:

```console
uv run --frozen smelt check --config smelt.yml --changed --fail-on warning --format json
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest
uv run --frozen pre-commit run --config .pre-commit-config.yml --all-files
uv run --frozen smelt check --config smelt.yml --fail-on warning
```

`uv run --frozen python scripts/check.py all` also runs the scoped transport type,
client drift and TypeScript runtime/coverage checks. Gates are `architecture`,
`lint`, `types`, `test`, `clients` and `browser`. All library Python tests share one
root pytest invocation; use qualified test/example imports. The reference service
also has a separate installed-application test invocation. Do not restore the old
colliding package-local `tests` and `examples` namespaces.

Use `smelt explain <rule-or-code>` for findings. CI and pre-commit enforce the full
policy and fail on warnings. Inspect hook fixes and rerun affected checks.
Generated clients are updated through generators/templates, then checked for
exact drift. Build/install checks use actual artifacts in fresh environments,
without source overrides. Only root `.github/workflows/` defines CI/releases.
Do not publish or archive repositories as part of routine development.

## Release preparation

Before 1.0, every breaking change requires the next minor release:
`0.x.y` becomes `0.(x+1).0`. Patch releases are only for backward-compatible
fixes and documentation. Count public API/signature changes, stricter accepted
inputs or validation, changed defaults and generated-client incompatibilities
as breaking changes, even when the wire format stays unchanged. Record them
under a `### Breaking changes` changelog heading with concrete migration steps.
Use the Canon version; do not classify a breaking change as a patch merely
because its implementation is small. Release validation checks declared breaking
changelog sections against this policy.

Read `docs/releases.md` for prepared versions, publication order, publisher setup
and the release workflow. `docs/compatibility.md` records tested dependency
combinations; `docs/rpc-parity.md` records the predecessor comparison and consumer
migration limits. Do not treat prepared source versions as published releases.

Run the standalone minimum-dependency smoke matrix with
`uv run --locked python scripts/check_distributions.py --python 3.12 --dependencies minimum`.
The default mode checks current compatible dependencies. Both modes create fresh
wheel environments and write dependency inventories under `dist/`. Check Streams'
installed CLI/backends with `uv run --locked python scripts/check_streams_artifacts.py`
and WebRTC's installed CLI and generated TypeScript client with
`uv run --locked python scripts/check_webrtc_artifacts.py` (after
`pnpm install --frozen-lockfile --ignore-scripts`).
For an external RPC contract, follow the installed-wheel command in the parity
report; SDK compilation alone does not establish a full application migration.

Use `release.toml` and `scripts/release.py` for package/tag/version metadata.
Prepare source versions and pins, regenerate clients, pass checks and commit/merge
to `main`. Wait for CI on that exact commit, then dispatch `release.yml` with
`dry_run=true` for the selected package/version. Before a real publication,
finalize the changelog date and pass CI for the final source. Only dispatch with
`dry_run=false` when explicitly authorized to publish. Verify public-index installs and release/tag results.
On failure, follow the documented resume procedure with the original run's
verified artifacts; do not replace published contents or move existing tags.
After verified publication, update the README's current-release table and the
release/compatibility records. All Canon releases belong in this
repository's central GitHub Releases list, under the `fastapi-canon-v` tag prefix.
Always create the matching GitHub Release for every package publication, including
changelog notes, the verified wheel/sdist and the release manifest. A publication
is not complete until its PyPI install, tag, GitHub Release and attached artifacts
have been verified. Do not treat a PyPI-only upload as a completed release.
