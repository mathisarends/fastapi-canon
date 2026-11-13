# Canon releases

Only the root `fastapi-canon` distribution is published. Integrations share its
version; the reference service is an example. `release.toml` and
`scripts/release.py` define release metadata.

## Current versions

**Published: 0.19.0 (2026-11-10).**

**Prepared: 0.20.0 (unpublished).** It raises the FastAPI minimum to 0.143.1,
adds typed contract selection, changes generated SSE namespace access and makes
package re-exported error paths visible to source checking. Follow the
[0.20.0 migration](package-migration.md#upgrade-to-0200) and
[changelog](../CHANGELOG.md#0200---unreleased). Exact-source CI and a release
dry run must pass after preparation is merged to `main`.

Published 0.19.0 includes RPC channel subclasses in source error-contract checking.
Follow the [migration](package-migration.md#upgrade-to-0190) and
[changelog](../CHANGELOG.md#0190---2026-11-10) before updating CI.

Published artifacts, manifests and run results are linked below. They record
verified publications; prepared source versions are not publication evidence.
Historical links retain their original repositories.

| Version | Source | CI | Dry run | Publication | Wheel, sdist and manifest |
| --- | --- | --- | --- | --- | --- |
| 0.19.0 (2026-11-10) | [Source](https://github.com/mathisarends/fastapi-canon/commit/f40ed640c99822abf2f7709ede1e16e0ea6fa8b2) | [CI](https://github.com/mathisarends/fastapi-canon/actions/runs/42853226604) | [Preparatory dry run](https://github.com/mathisarends/fastapi-canon/actions/runs/42850938534) | [Publish](https://github.com/mathisarends/fastapi-canon/actions/runs/42855022556) | [Assets](https://github.com/mathisarends/fastapi-canon/releases/tag/fastapi-canon-v0.19.0) |
| 0.18.0 (2026-11-10) | [Source](https://github.com/mathisarends/fastapi-canon/commit/f4fa6096ff9c18c2fe5df32c2cae40ee0d9ec969) | [CI](https://github.com/mathisarends/fastapi-canon/actions/runs/42785593526) | [Dry run](https://github.com/mathisarends/fastapi-canon/actions/runs/42787033263) | [Publish](https://github.com/mathisarends/fastapi-canon/actions/runs/42788045597) | [Assets](https://github.com/mathisarends/fastapi-canon/releases/tag/fastapi-canon-v0.18.0) |
| 0.17.0 (2026-11-10) | [Source](https://github.com/mathisarends/fastapi-canon/commit/9745a85fe6f16852a7eee16161be73d6a3703fbd) | [CI](https://github.com/mathisarends/fastapi-canon/actions/runs/42719244150) | [Earlier dry run](https://github.com/mathisarends/fastapi-canon/actions/runs/42717728251) | [Publish](https://github.com/mathisarends/fastapi-canon/actions/runs/42721013782) | [Assets](https://github.com/mathisarends/fastapi-canon/releases/tag/fastapi-canon-v0.17.0) |
| 0.16.0 (2026-11-07) | [Source](https://github.com/mathisarends/fastapi-canon/commit/5b36111a1b5750424052a9af96e8bbbed07bc0cc) | [CI](https://github.com/mathisarends/fastapi-canon/actions/runs/42382306017) | [Dry run](https://github.com/mathisarends/fastapi-canon/actions/runs/42383067506) | [Publish](https://github.com/mathisarends/fastapi-canon/actions/runs/42383550071) | [Assets](https://github.com/mathisarends/fastapi-canon/releases/tag/fastapi-canon-v0.16.0) |
| 0.15.0 (2026-11-04) | [Source](https://github.com/mathisarends/fastapi_canon/commit/e4abb2c54706854d9ec7b0c4cb668b9cbfa23545) | [CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41883371046) | [Dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41884977604) | [Publish](https://github.com/mathisarends/fastapi_canon/actions/runs/41886079014) | [Assets](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.15.0) |
| 0.14.0 (2026-11-03) | [Source](https://github.com/mathisarends/fastapi_canon/commit/8e90a68315dcc311a9ac64a1ab09ea1315d4fc38) | [CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41697444397) | [Dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41698678199) | [Publish](https://github.com/mathisarends/fastapi_canon/actions/runs/41699504831) | [Assets](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.14.0) |
| 0.13.0 (2026-11-02) | [Source](https://github.com/mathisarends/fastapi_canon/commit/14a723ae6bbd07062c72240bb9edd4c6b636f5d2) | [CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41536578334) | [Dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41537793910) | [Publish](https://github.com/mathisarends/fastapi_canon/actions/runs/41538502889) | [Assets](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.13.0) |
| 0.12.0 (2026-11-02) | [Source](https://github.com/mathisarends/fastapi_canon/commit/703fce8a769253c604b0e4f1901e69f43aac42b9) | [CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41427274311) | [Dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41428297603) | [Publish](https://github.com/mathisarends/fastapi_canon/actions/runs/41428704283) | [Assets](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.12.0) |
| 0.11.0 (2026-11-01) | [Source](https://github.com/mathisarends/fastapi_canon/commit/2773b56498fad05ac9e1f1163ee33241b6d70eff) | [CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41346976457) | [Dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41347623801) | [Publish](https://github.com/mathisarends/fastapi_canon/actions/runs/41348067349) | [Assets](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.11.0) |
| 0.10.0 (2026-11-01) | [Source](https://github.com/mathisarends/fastapi_canon/commit/4d9ce1842b350a9bf867d512cc89aafc4b44d53b) | [CI](https://github.com/mathisarends/fastapi_canon/actions/runs/41311732023) | [Dry run](https://github.com/mathisarends/fastapi_canon/actions/runs/41312304534) | [Publish](https://github.com/mathisarends/fastapi_canon/actions/runs/41312590718) | [Assets](https://github.com/mathisarends/fastapi_canon/releases/tag/fastapi-canon-v0.10.0) |

The 0.19.0 and 0.17.0 preparatory dry runs predate changelog finalization. Publication
revalidated each final dated source; its public installs and attached artifacts
are verified.

Recovery notes: 0.19.0, 0.18.0, 0.15.0 and 0.14.0 needed public-index verification after
stale index results. The 0.19.0 retry invalidated the stale uv cache and reused
the original verified artifacts; 0.11.0 needed GitHub finalization retried after incomplete PyPI
metadata. Version 0.10.0 required publisher configuration and local public-index
verification before finalization. All recoveries retained the original artifact
bytes and source. Version 0.10.0 includes the unpublished 0.9.0 migrations.
See [compatibility](compatibility.md) for tested scope.

Older publication evidence:

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

## Publisher setup

The PyPI Trusted Publisher uses owner `mathisarends`, repository `fastapi-canon`,
workflow `release.yml` and environment `pypi`; the environment permits `main`.
Publishing uses job-scoped OIDC. See the
[repository cutover](repository-migration.md#cutover) for historical setup.

## Prepare a version

1. Classify changes. Before 1.0, breaking changes require the next minor version
   with patch zero. Public signatures, stricter inputs/checks, defaults and client
   incompatibilities count. Record concrete migrations under `### Breaking changes`.
2. Update `pyproject.toml`, `fastapi_canon/_version.py`, self-pins, the reference
   service range and `uv.lock`. Regenerate committed clients through generators.
3. Run the repository, browser, current/minimum wheel and installed CLI checks
   listed in [contributing](contributing.md).
4. Validate metadata locally, then merge to `main` and wait for the complete
   `ci.yml` push run on that exact commit.

```console
uv run --locked python scripts/release.py validate --package fastapi-canon --version NEXT_VERSION
```

Local validation checks versions, pins, lockfile and changelog. The release
workflow additionally checks main ancestry, clean source, exact-commit CI, tags
and PyPI state. Local success alone does not establish publication readiness.

## Dispatch a dry run

```console
gh workflow run release.yml --repo mathisarends/fastapi-canon --ref main -f package=fastapi-canon -f version=NEXT_VERSION -f dry_run=true
gh run list --repo mathisarends/fastapi-canon --workflow release.yml --limit 10
```

Preparation builds and checks the selected wheel/sdist in fresh environments,
with current and minimum direct dependencies and installed SSE/WebRTC CLI checks.
The `release-fastapi-canon-VERSION` artifact contains `release/` and
`release-report/`: hashes, source/run links, check results, manifest and notes.
Notes include consecutive older unreleased sections; documentation links point
to the verified source commit. Actions retains preparation artifacts for 90 days.
Dry runs have no publication or repository write permissions.

## Publish when explicitly authorized

Finalize the changelog date as `## VERSION - YYYY-MM-DD`, commit it and pass
exact-source CI again. Dispatch a real publication only with explicit authorization:

```console
gh workflow run release.yml --repo mathisarends/fastapi-canon --ref main -f package=fastapi-canon -f version=NEXT_VERSION -f dry_run=false
```

The publish job verifies and uploads the prepared bytes from a separate staging
directory. Existing PyPI files must match the manifest. Public-index checks cover
core, uploads, database, telemetry, MCP and transports, including uploads with
codegen and the combined installation. They exercise actual installed behavior.

The finish job creates `fastapi-canon-vVERSION` and a matching GitHub Release
at the verified commit, with notes, wheel, sdist and manifest attached.
Publication is complete only after PyPI installs, hashes, tag, release and assets
are verified. Prereleases receive prerelease GitHub Releases.

## Resume and record the outcome

- Retry transient failures on unchanged source with
  `gh run rerun RUN_ID --failed --repo mathisarends/fastapi-canon`.
  Resume historical runs in the repository named by their original manifest,
  including `mathisarends/canon`.
- After upload or finalization failure, retry failed jobs on the original run.
  Upload only missing original files; verify complete uploads without replacing
  them. Never rebuild a published version or move an existing tag.
- A source fix requires a new commit, exact-source CI and a new preparation run.
  Matching releases are accepted on retry; conflicting tags or bytes fail.
- Keep root workflows unchanged until publication finishes. Recovery after a
  workflow change may require an already authorized CLI login with workflow-write
  permission; use the original manifest, source, notes and artifacts. See
  [GitHub's permission note](https://docs.github.com/en/rest/releases/releases#create-a-release).
- After verification, update the README's published version and compatibility
  records. Keep detailed logs, hashes and manifests in Actions and GitHub Releases.
