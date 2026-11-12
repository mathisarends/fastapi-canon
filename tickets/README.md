# Developer experience tickets

Existing tickets were reviewed against `origin/main` at `452a4b0`
(2026-11-07); newer proposals record their own baseline. Completed work and
decisions are documented in the guides and their PRs.

| Ticket | Kind | Status |
| --- | --- | --- |
| [DX-028](DX-028-dependency-version-overview.md) | Documentation | Open |
| [DX-029](DX-029-transport-diagnostic-profile.md) | Decision | Open |
| [DX-031](DX-031-websocket-facade-reduction.md) | Feature (breaking) | Open |
| [DX-033](DX-033-target-contract-generation.md) | API proposal ([#154](https://github.com/mathisarends/fastapi-canon/issues/154)) | Discussion |

The removed tickets no longer require active tracking:

- DX-018 is covered by [multipart uploads and binary transfers](../docs/integrations/uploads.md).
- DX-017 and DX-019 are superseded by the [quickstart](../docs/quickstart.md)
  and [runnable example](../examples/canonical_errors/README.md). DX-017's
  remaining proposal to commit a combined generated SDK is dropped; generation
  and consumer examples remain available without that additional artifact.
- DX-020 through DX-024, DX-026 and DX-027 record completed decisions from
  [#99](https://github.com/mathisarends/fastapi-canon/pull/99) and
  [#102](https://github.com/mathisarends/fastapi-canon/pull/102). The still-open
  WebSocket facade proposal is retained in DX-031.
- DX-025 is implemented by [#124](https://github.com/mathisarends/fastapi-canon/pull/124);
  see [single-app generation](../docs/codegen.md#configuration).
- DX-030 and DX-032 are implemented by
  [#105](https://github.com/mathisarends/fastapi-canon/pull/105) and
  [#115](https://github.com/mathisarends/fastapi-canon/pull/115);
  see [public namespaces](../docs/canon.md#public-namespaces) and the
  [0.16.0 migration](../docs/package-migration.md#upgrade-to-0160).

## Shared acceptance and verification rules

Every implementation must also satisfy these rules:

- Read [architecture](../docs/architecture.md) and `smelt.yml` before changing
  boundaries or test structure. Use `uv run --frozen smelt context <path>`
  before implementation. Source/style policies belong in Smelt/Ruff.
- Verify actual APIs in the checkout. Consumer examples use documented facades;
  do not access private production members to make tests pass.
- Mirror tests under their existing roots; use real collaborators in integration
  and contract tests, finite local deadlines, and observable outcomes.
- Preserve core imports/help without optional runtimes. Do not add eager media
  imports, sibling-transport dependencies or another package workspace.
- Apply the [release policy](../AGENTS.md#release-preparation). Stricter accepted
  configurations/validation, changed defaults, public signatures and generated
  client incompatibilities require the next minor release before 1.0. At this
  review the published version is 0.16.0 and the next minor is 0.17.0; recheck
  current release state when implementing. Include Breaking changes migration
  notes when required; never classify work solely by its line count.
- Generate client changes through generators/templates, then verify exact drift.
  Do not edit generated output by hand.
- Run the root checks from [contributing](../docs/contributing.md) and AGENTS.md,
  plus the selected ticket's focused verification. Record commands, outcomes
  and any uncompleted checks in the implementation PR.
- No ticket authorizes publication, retagging, repository archival or a release
  workflow dispatch.

Runnable source-checker repros may be saved under ignored
`build/ticket-repros/<ticket>/src/` while investigating. Keep each source root
separate and use unique app/module names. Permanent demos belong in examples/,
shared fixtures in tests/fixtures/, and development scripts in scripts/.

## Adding another ticket

Use [TEMPLATE.md](TEMPLATE.md). Keep IDs stable and add one row here.
