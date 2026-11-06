# Developer experience tickets

Remaining tickets and decision records. Completed implementation tickets are
documented in their merged PRs.

| Ticket | Kind | Status |
| --- | --- | --- |
| [DX-017](DX-017-combined-sdk-example.md) | Documentation and fixture | Open |
| [DX-018](DX-018-multipart-support-boundary.md) | Decision | Open |
| [DX-019](DX-019-getting-started-path.md) | Documentation | Open |
| [DX-020](DX-020-mcp-activation-policy.md) | Decision | Done ([#99](https://github.com/mathisarends/fastapi-canon/pull/99)) |
| [DX-021](DX-021-composition-prefix-policy.md) | Decision | Done ([#99](https://github.com/mathisarends/fastapi-canon/pull/99)) |
| [DX-022](DX-022-feature-metadata-defaults.md) | Decision | Done ([#99](https://github.com/mathisarends/fastapi-canon/pull/99)) |
| [DX-023](DX-023-public-export-organization.md) | Decision | Done ([#102](https://github.com/mathisarends/fastapi-canon/pull/102)) |
| [DX-024](DX-024-generation-command-policy.md) | Decision and documentation | Done ([#99](https://github.com/mathisarends/fastapi-canon/pull/99)) |
| [DX-025](DX-025-single-app-codegen-config.md) | Decision | Open |
| [DX-026](DX-026-mcp-problem-contracts.md) | Decision | Done ([#99](https://github.com/mathisarends/fastapi-canon/pull/99)) |
| [DX-027](DX-027-context-terminology.md) | Decision | Done ([#99](https://github.com/mathisarends/fastapi-canon/pull/99)) |
| [DX-028](DX-028-dependency-version-overview.md) | Documentation | Open |
| [DX-029](DX-029-transport-diagnostic-profile.md) | Decision | Open |
| [DX-030](DX-030-root-facade-reduction.md) | Feature (breaking) | Open |
| [DX-031](DX-031-websocket-facade-reduction.md) | Feature (breaking) | Open |
| [DX-032](DX-032-transport-router-adapters.md) | Feature (breaking) | Open |

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
  baseline the published version is 0.15.0 and the next minor is 0.16.0; recheck
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
