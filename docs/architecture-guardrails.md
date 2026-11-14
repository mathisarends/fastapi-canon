# Architecture and test guardrails

[smelt.yml](../smelt.yml) defines dependency boundaries, source conventions and
test policies. The development lockfile pins `smelt-cli==0.6.0`;
[.smelt.schema.json](../.smelt.schema.json) provides editor completion.

## Run the policy

```console
uv run --frozen smelt context fastapi_canon.error.registry
uv run --frozen smelt check --config smelt.yml --changed --fail-on warning
uv run --frozen smelt check --config smelt.yml --fail-on warning
uv run --frozen smelt explain SMT423
```

The full check runs in CI, `scripts/check.py all` and pre-commit.
Warnings fail, including the enabled source conventions for definition order,
optional defaults, callable operations, discriminators, provider registrations
and facade ownership. Use `--show-coverage` for the source inventory.
Changed-file checks still enforce complete coverage.

The `information-hiding` group checks implementation functions, constants and
attributes, including APIs used only by tests. Existing error-level checks remain
errors. Private literal class constants belong at module scope. Repository-owned
YAML uses `.yml`, and Compose files use `compose` as their basename; pnpm's
lockfile and the agent skill's `openai.yaml` retain their required names.

Declare `StrEnum` members with explicit string literals. Their wire and public
values must remain visible and independent of Python member names; do not use
`auto()` for these enums.

Inspect public contracts and falsy inputs before changing
names, dependency lifetimes or defaults; syntax alone cannot establish that a
change preserves compatibility. `AcquireTransactionLock`, for example, provides
a callable operation while retaining the supported `TransactionLocks` alias and
named `acquire` operation.

## Change the policy

Keep architectural boundaries in Smelt and style rules in Ruff. Do not reproduce
them as AST-based pytest tests. Read [architecture](architecture.md) before
changing package roles or test structure.

Fix a finding or document a narrow, concrete exception beside the affected code.
Do not add debt baselines, broad exclusions or weaker rules to pass checks.
Unused suppressions fail. Preserve public compatibility while correcting a
finding. Shared exports must have a consumer contract; retain supported aliases
when an operation acquires a clearer callable interface.

Tests must assert observable outcomes, use bounded waits and subprocesses, and
avoid private production access. Integration and contract tests prohibit mocks;
unit tests normally allow one object-targeted patch. The configuration records
the few existing exceptions and their reasons.

## Upgrade Smelt

Regenerate the editor schema as UTF-8 and review the
[upstream rule catalog](https://github.com/mathisarends/smelt/blob/v0.6.0/docs/rules/README.md)
and [upgrade guide](https://github.com/mathisarends/smelt/blob/v0.6.0/docs/upgrading.md#050-to-060).
Run the full policy and inspect new findings before adopting rules.
Keep suppressions and exclusions tied to actual contracts.

For agent-assisted policy work, install the release's official skill and offline
rule references in the local checkout:

```console
uv run --frozen smelt skill install
```

After an upgrade, preserve any local skill customizations before refreshing with
`uv run --frozen smelt skill install --force`. Keep Canon-specific policy in
`smelt.yml` and repository instructions.

See [contributing](contributing.md) for the remaining checks.
