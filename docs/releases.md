# Canon releases

Release the unified `fastapi-canon` distribution from `mathisarends/fastapi-canon`.
The root project is the sole publication target; the reference service is an
installable example. `release.toml` maps package
names, directories, modules and tag prefixes; `scripts/release.py` validates them.
Published versions and their GitHub/PyPI assets are recorded under
[current versions](#current-versions). The root [changelog](../CHANGELOG.md)
contains migration notes; linked GitHub Releases and Actions retain publication
evidence. Run all development and release-preparation commands below from the
repository root.
The [repository migration](repository-migration.md) records the cutover. The
historical runs, tags and assets linked below remain in their original repositories.

## Prepared 0.16.0 (unpublished)

Source metadata, internal pins and the reference service target 0.16.0. This is
the next minor release required by the breaking changes since 0.15.0. Follow the
[upgrade guide](package-migration.md#upgrade-to-0160) and regenerate both SDKs.
Publication is authorized and the changelog date is finalized as 2026-11-07.
The final source must still pass exact-commit CI and the release dry run before
publication. No 0.16.0 publication is recorded here.

The GitHub repository is now `mathisarends/fastapi-canon`. Release metadata and
active commands use that spelling; historical publication URLs below retain
their original spelling. The owner confirmed that the PyPI Trusted Publisher
now names repository `fastapi-canon`, workflow `release.yml` and environment
`pypi`. The publication workflow will verify the OIDC exchange. See the
[repository rename](repository-migration.md#repository-rename).

## Current versions

Canon **0.15.0** was published on 2026-11-04 from
[verified source `e4abb2c`](https://github.com/mathisarends/fastapi_canon/commit/e4abb2c54706854d9ec7b0c4cb668b9cbfa23545),
after [exact-source CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41883371046)
and the [release dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41884977604).
The [publication run](https://github.com/mathisarends/fastapi_canon/actions/runs/41886079014)
passed both eighteen-combination wheel matrices and installed SSE/WebRTC CLI and
client checks, then uploaded its original verified wheel and sdist. Its public-index
check failed because the runner's package index still omitted the new version.
Recovery retained the original source, notes, manifest and artifact bytes. The
documented `verify-pypi` command passed all thirteen public-index installation
combinations locally on Windows/Python 3.14, including MCP; `finish` created the
matching tag and GitHub Release from the original run artifacts.
The [GitHub Release](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.15.0)
contains the original wheel, sdist and manifest. Downloaded GitHub assets, PyPI
file hashes and both run manifests agree; the release notes match the changelog
and the tag points to the verified source. Dry-run and publication artifacts
have identical hashes. Distribution files were neither rebuilt nor replaced.
A fresh Windows/Python 3.12 public-index installation also passed the combined
HTTP/SSE/WebSocket/WebRTC/telemetry/MCP smoke.

This release unifies WebSocket message and binary stream declarations and adds
managed Python/TypeScript SDK stream resources. Regenerate both SDKs and upgrade
them with the server. Optional MCP feature mounts add FastMCP and Dishka injection
without changing core dependency requirements. Follow the
[0.15.0 upgrade guide](package-migration.md#upgrade-to-0150),
[breaking changes](../CHANGELOG.md#0150---2026-11-04) and
[MCP guide](integrations/mcp/README.md).

Canon **0.14.0** was published on 2026-11-03 from
[verified source `8e90a68`](https://github.com/mathisarends/fastapi_canon/commit/8e90a68315dcc311a9ac64a1ab09ea1315d4fc38),
after [exact-source CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41697444397)
and the [release dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41698678199).
The [publication run](https://github.com/mathisarends/fastapi_canon/actions/runs/41699504831)
passed both seventeen-combination wheel matrices, installed SSE/WebRTC CLI and
client checks, and all twelve public-index installation combinations. The
[GitHub Release](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.14.0)
contains the original wheel, sdist and manifest. Downloaded assets, PyPI file
hashes, the manifest and release notes agree; the tag points to the verified source.
Public-index verification resumed on the original run after clearing stale CI
index caches; no distribution files were rebuilt or replaced.

This release adds configurable startup migration checks, per-engine contexts and
`SqlAlchemyInstrumentation`, explicit Alembic downgrades and the installable
`build-canon-backend` skill. Database integration remains opt-in; select its
required extras explicitly. Follow the
[0.14.0 upgrade guide](package-migration.md#upgrade-to-0140) and
[breaking changes](../CHANGELOG.md#0140---2026-11-03).

Canon **0.13.0** was published on 2026-11-02 from
[verified source `14a723a`](https://github.com/mathisarends/fastapi_canon/commit/14a723ae6bbd07062c72240bb9edd4c6b636f5d2),
after [exact-source CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41536578334)
and the [release dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41537793910).
The [publication run](https://github.com/mathisarends/fastapi_canon/actions/runs/41538502889)
passed both seventeen-combination wheel matrices, installed SSE/WebRTC CLI and
client checks, and all twelve public-index installation combinations. The
[GitHub Release](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.13.0)
contains the original wheel, sdist and manifest. Downloaded assets, PyPI file
hashes, the manifest and release notes agree; the tag points to the verified source.

This release adds request logging, SQLite/PostgreSQL integration, Alembic
migrations and short service scopes. Migrate positional features to
`Composition(features=[first, second])` and WebSocket server composition to
`CanonWebSocketRouter`. HTTP scopes close before responses are sent; WebSocket
controllers start independent traces when messages carry no parent context.
Follow the [0.13.0 upgrade guide](package-migration.md#upgrade-to-0130) and
[breaking changes](../CHANGELOG.md#0130---2026-11-02).

Canon **0.12.0** was published on 2026-11-02 from
[verified source `703fce8`](https://github.com/mathisarends/fastapi_canon/commit/703fce8a769253c604b0e4f1901e69f43aac42b9),
after [exact-source CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41427274311)
and the [release dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41428297603).
The [publication run](https://github.com/mathisarends/fastapi_canon/actions/runs/41428704283)
passed both thirteen-combination wheel matrices, installed Streams/WebRTC
CLI/client checks and all eight public-index installation combinations,
including the new telemetry resource extras. The
[GitHub Release](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.12.0)
contains the original wheel, sdist and manifest. Its tag points to the verified
source; downloaded GitHub assets, PyPI hashes, release notes and the manifest
agree. The real publication and dry run produced identical distribution hashes.

This release adds explicit HTTPX and SQLAlchemy telemetry adapters,
composition-owned `Telemetry` injection and tuple-based `instrument()` declarations.
See the
[0.12.0 migration notes](../CHANGELOG.md#0120---2026-11-02).

Canon **0.11.0** was published on 2026-11-01 from
[verified source `2773b56`](https://github.com/mathisarends/fastapi_canon/commit/2773b56498fad05ac9e1f1163ee33241b6d70eff),
after [exact-source CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41346976457)
and the [release dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41347623801).
The [publication run](https://github.com/mathisarends/fastapi_canon/actions/runs/41348067349)
passed both eleven-combination wheel matrices, installed CLI/client checks and
all six public-index installation combinations. Its GitHub finalization job was
rerun once on the original run after the PyPI API briefly reported an incomplete
release; no files were rebuilt or replaced. The
[GitHub Release](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.11.0)
contains the original wheel, sdist and manifest; its tag points to the verified source.
Downloaded GitHub assets, PyPI hashes, the manifest and changelog-derived release
notes agree.

This release replaces `observe()` with feature-owned `instrument()` declarations
and includes the generated Python RPC client fix, shared pnpm tooling and expanded
core/SDK guides.
See the [0.11.0 changelog](../CHANGELOG.md#0110---2026-11-01)
for migration steps, including client regeneration.

Canon 0.10.0 was published on 2026-11-01 from
[verified source `4d9ce18`](https://github.com/mathisarends/fastapi_canon/commit/4d9ce1842b350a9bf867d512cc89aafc4b44d53b),
after [exact-source CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41311732023)
and the [final dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41312304534).
It includes the unpublished 0.9.0 consolidation and its migrations; 0.9.0 was
never published separately. The
[publication run](https://github.com/mathisarends/fastapi_canon/actions/runs/41312590718)
uploaded its original verified wheel and sdist after correcting the Trusted
Publisher's repository/environment fields. Its public-install check then failed
because the publish job had not installed the npm compiler dependencies.

Recovery kept the original source, notes, manifest and artifact bytes. The
documented `verify-pypi` command passed all six public-index installation
combinations locally on Windows/Python 3.14; the combined extras also passed a
fresh Windows/Python 3.12 public-index installation. The documented `finish`
command created the matching
[tag and GitHub Release](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.10.0).
Downloaded GitHub assets, PyPI file hashes and the original manifest agree, and
the tag points to the verified source. The publish job now installs Node.js and
the shared pnpm TypeScript tooling before public-install verification.

Streams 0.5.0, Canon 0.8.1 and RPCKit 0.4.1 were published on 2026-10-25 from
[verified source `ebd725b`](https://github.com/mathisarends/canon/commit/ebd725b14610f4017614281d9ad074920cf6f002),
after [exact-source CI](https://github.com/mathisarends/canon/actions/runs/40237725498). Streams was published
before Canon, which pins it. Publication workflows:
[Streams](https://github.com/mathisarends/canon/actions/runs/40238129418),
[Canon](https://github.com/mathisarends/canon/actions/runs/40238523107) and
[RPCKit](https://github.com/mathisarends/canon/actions/runs/40238531843). The RPCKit finalization job was rerun
once on the original run after the PyPI API briefly reported an incomplete
upload; no files were replaced. PyPI hashes match the GitHub Release assets and
manifests, each tag points to the verified source, and a fresh Windows/Python 3.12
public-index installation of all four current package versions passed import and
CLI smoke checks.

Before that, Canon 0.8.0 was published on 2026-10-25 from
[verified source `50b8e4b`](https://github.com/mathisarends/canon/commit/50b8e4b442b93ad75b8e2b36024487e38f308330),
after [exact-source CI](https://github.com/mathisarends/canon/actions/runs/40213500982)
and the [publication workflow](https://github.com/mathisarends/canon/actions/runs/40214446851).

Previously, WebRTC 0.3.0 and Canon 0.7.0 were published on 2026-10-24 from
[verified source `6aad274`](https://github.com/mathisarends/canon/commit/6aad274800d1960a8bbbcb0fd7d54ec3f6901c99),
in that order. Both publication workflows completed successfully:
[WebRTC](https://github.com/mathisarends/canon/actions/runs/40079433426) and
[Canon](https://github.com/mathisarends/canon/actions/runs/40079766254).
Each has a matching tag and GitHub Release with its original wheel, sdist and
manifest. PyPI file hashes, public installs, release notes and downloaded GitHub
assets are verified.

| Package | Version | Migration |
| --- | --- | --- |
| Canon | 0.15.0 | Regenerate Python and TypeScript SDKs with the server; replace `subscription()` with `stream()`, `.subscribe`/`.unsubscribe` with `.open`/`.cancel`, and `max_subscriptions` with `max_streams`. Follow the [0.15.0 upgrade guide](package-migration.md#upgrade-to-0150); MCP remains opt-in. Older upgrades also require the [0.14.0 database controls](package-migration.md#upgrade-to-0140), [0.13.0 composition/scope migration](package-migration.md#upgrade-to-0130), [0.12.0](../CHANGELOG.md#0120---2026-11-02), [0.11.0](../CHANGELOG.md#0110---2026-11-01), [0.10.0](../CHANGELOG.md#0100---2026-11-01) and included [0.9.0 migrations](../CHANGELOG.md#090---unreleased). |
| WebRTC | 0.3.0 | Use public enum members, keyword-only engine/channel options and `DescriptionType`; handle lazy type aliases in introspection tools. See the [migration notes](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-webrtc.md#030---2026-10-24). |
| Streams | 0.5.0 | Regenerate clients. SDK-owned generated Python clients bound the connect time to 10 seconds; pass `RequestOptions(timeout=None)` or inject `httpx.AsyncClient(timeout=None)` to keep it unbounded. See the [Streams 0.5.0 notes](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-streams.md#050---2026-10-25). |
| RPCKit | 0.4.1 | None; wire output and generated clients are unchanged apart from the manifest generator version. See the [RPCKit 0.4.1 notes](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-rpckit.md#041---2026-10-25). |

The earlier Canon 0.8.1 and transport publications used separate distributions.
Their consolidation ships in Canon 0.10.0 without internal distribution pins.

### Consumer integration upgrade

The 2026-10-20 consumer integration batch addressed feedback for Canon 0.5.5,
WebRTC 0.1.0 and RPCKit 0.3.0:

- [Canon admission errors](webrtc.md) reuse named
  errors, validate registry membership and export declared dependency/factory
  failures in session contracts. HTTP denials happen before peer allocation;
  accepted sessions retain WebRTC protocol errors.
- [Remote engine bindings and leases](../examples/webrtc/remote_video/README.md)
  keep each viewer's resources independent, including cancellation and rejected
  second viewers. The example documents native error mapping and partial stats.
- [Browser video health](integrations/webrtc/media-health.md) observes
  decoded or compositor frames with optional first-frame and stall deadlines.
  Applications retain control of playback and fallback. The optional
  [HTTP ticket recipe](integrations/webrtc/admission-tickets.md) provides
  readable admission errors before signaling.
- [RPCKit factory typing](https://github.com/mathisarends/fastapi_canon/blob/21c74910ea7304cb136af7bfb3953dcafcc2325d/docs/history/fastapi-rpckit.md#040---2026-10-20)
  removes the URL-parameter and backend subprotocol mismatches. Regenerate clients
  before removing consumer generator overrides.

The signaling protocol stays `fastapi-webrtc.v1`. A maintained CDP binding and
deployed TURN/NAT, database concurrency and proxy validation remain separate work.

## Unified release scope

Canon 0.10.0 includes the unpublished 0.9.0 package consolidation and optional
native OpenTelemetry integration, plus canonical error bindings and common SDK
generation. See the [migration guide](package-migration.md).
Only Canon is an active release target. Integrations now live under
`fastapi_canon/integrations/`, tests under `tests/`, examples under `examples/`
and historical changelogs in Git history. Source metadata alone does not establish
a publication; the verified 0.10.0 publication is recorded above.

## Publisher setup

The required Trusted Publisher uses GitHub owner `mathisarends`, repository
`fastapi-canon`, workflow filename `release.yml` and environment `pypi`. Configure
it on the existing PyPI `fastapi-canon` project before publishing from this
repository; source changes do not update PyPI account settings. The GitHub
environment permits `main`. See the [cutover checklist](repository-migration.md#cutover).
Publishing uses job-scoped OIDC; no persistent PyPI token is needed. Only root
workflows are active. Every real publication requires explicit user authorization.

## Prepare a version

Before 1.0, breaking changes advance `0.x.y` to `0.(x+1).0`. Patch versions are
for backward-compatible fixes and documentation. Public signatures, accepted
inputs, stricter validation, defaults and generated-client contracts count even
when wire formats do not change. Use a `### Breaking changes` changelog heading
with concrete migration steps. Release validation enforces the next minor and
patch zero for declared breaking changes.

1. Update the package version, changelog and existing `__version__` constant.
   Canon and its feature modules share `fastapi_canon/_version.py`.
2. Update root pins, Canon's feature dependency ranges and the
   reference service's Canon range as needed. Run `uv lock` at the root.
3. Regenerate clients through their generators. Run the repository, browser,
   current/minimum wheel and installed CLI checks in [contributing](contributing.md).
4. Commit/merge to `main` and wait for the complete `ci.yml` push run on that exact
   commit. Then dispatch the selected package's dry run.
5. Before an authorized publication, finalize the changelog heading as
   `## VERSION - YYYY-MM-DD`, commit/push and pass exact-source CI again.

Local metadata validation does not establish remote publication readiness:

```console
uv run --locked python scripts/release.py validate --package fastapi-canon --version NEXT_VERSION
```

## Dispatch a dry run

```console
gh workflow run release.yml --repo mathisarends/fastapi-canon --ref main -f package=fastapi-canon -f version=NEXT_VERSION -f dry_run=true
gh run list --repo mathisarends/fastapi-canon --workflow release.yml --limit 10
```

Choose the package and its prepared, unpublished version. Use the workflow filename
without `.github/workflows/`. Canonical prereleases are supported; local/dev/post
versions are not. A new run rejects a version already on PyPI.

Preparation verifies metadata, constants, pins, changelog, lockfile, main ancestry,
exact-commit CI, tags and PyPI state. Only the selected wheel/sdist enters
`dist/release/`; other packages are built as test dependencies. Fresh environments
exercise those upload artifacts with current dependencies and direct minima on
Python 3.12, plus installed Streams/WebRTC CLI and generated-client checks.

Each run uploads `release-PACKAGE-VERSION` containing `release/` and
`release-report/`: the package/version/commit/tag, CI/run links, check results,
SHA-256 hashes and changelog-derived notes. Notes include the selected version and
consecutive older sections marked `unreleased`, stopping at the first published
section. Thus the 0.10.0 notes carry the 0.9.0 migrations when upgrading from 0.8.1,
even after the 0.10.0 changelog date is finalized. Relative documentation links
resolve against the verified source commit. Artifacts are retained for 90 days.
Dry runs have neither OIDC nor repository write permissions and skip publication
and finalization. Preparation artifacts can be replaced on a preparation retry;
publication uses the successful run's original verified bytes.

## Publish when explicitly authorized

After final changelog dates and exact-source CI, dispatch the selected package
with `dry_run=false` only when publication is explicitly authorized:

```console
gh workflow run release.yml --repo mathisarends/fastapi-canon --ref main -f package=fastapi-canon -f version=NEXT_VERSION -f dry_run=false
```

The `publish` job downloads and verifies the prepared artifacts and uploads only
the wheel/sdist from a separate staging directory. Signature sidecars created by
the publisher must not modify the canonical artifact directory. Existing PyPI
files must match the original manifest; conflicting contents fail. Canon also
contains all transport modules and their generator resources in one artifact set.

After upload, verify hashes and fresh public-index installs. Canon checks core,
`[otel]`, `[otel-httpx]`, `[otel-sqlalchemy]`, `[mcp]`, `[sse]`, `[websockets]`, `[webrtc]`
and combined feature/tool extras. The thirteen installation combinations also
cover `[database]`, `[sqlite,migrations]`, `[sqlite,migrations,sqlmodel]` and
`[postgres,migrations]`. SQLite combinations exercise transactions and real
Alembic upgrades; live PostgreSQL behavior is verified by the CI database job.
The separate `finish` job then
creates the package-prefixed tag and central GitHub Release at the verified
commit, with changelog notes, original wheel/sdist and manifest attached.
Prereleases produce prerelease GitHub Releases. A PyPI-only upload is incomplete.

## Resume and record the outcome

- A source fix requires a new commit, exact-source CI and a new run. Retry a
  transient failure on unchanged source with
  `gh run rerun RUN_ID --failed --repo mathisarends/fastapi-canon`.
  Historical publication runs must be resumed in the repository recorded by
  their original manifest, including `mathisarends/canon` for its past releases.
- After upload/finalization failure, rerun only failed jobs on the original run.
  Partial uploads send missing original files; complete uploads are verified
  without reuploading. Never rebuild under a published version or move a tag.
- Matching releases are accepted on retry; a tag at another commit is rejected.
- Keep root workflows unchanged until a release finishes. If they change, an
  older source's release can require an already authorized CLI login with
  workflow-write permission. Use the manifest's exact commit, original notes,
  wheel, sdist and report, then retry finalization. See
  [GitHub's release API permission note](https://docs.github.com/en/rest/releases/releases#create-a-release).
- After verified publication, update the README's current versions and
  compatibility table. Keep per-run logs, manifests and checksums in Actions and
  the matching GitHub Release rather than adding development transcripts here.
