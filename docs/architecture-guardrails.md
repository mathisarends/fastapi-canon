# Architecture and test guardrails

[smelt.yml](../smelt.yml) defines dependency boundaries, source conventions and
test policies. The development lockfile pins `smelt-cli==0.5.0`;
[.smelt.schema.json](../.smelt.schema.json) provides editor completion.

## Run the policy

```console
uv run --frozen smelt context fastapi_canon.error.registry
uv run --frozen smelt check --config smelt.yml --changed --fail-on warning
uv run --frozen smelt check --config smelt.yml --fail-on warning
uv run --frozen smelt explain SMT423
```

The full check runs in CI, `scripts/check.py all` and pre-commit.
Warnings fail. Use `--show-hints` for advisory findings and `--show-coverage`
for the source inventory. Changed-file checks still enforce complete coverage.

## Change the policy

Keep architectural boundaries in Smelt and style rules in Ruff. Do not reproduce
them as AST-based pytest tests. Read [architecture](architecture.md) before
changing package roles or test structure.

Fix a finding or document a narrow, concrete exception beside the affected code.
Do not add debt baselines, broad exclusions or weaker rules to pass checks.
Unused suppressions fail. Advisory findings alone do not justify changing public
APIs or adding exports.

Tests must assert observable outcomes, use bounded waits and subprocesses, and
avoid private production access. Integration and contract tests prohibit mocks;
unit tests normally allow one object-targeted patch. The configuration records
the few existing exceptions and their reasons.

## Upgrade Smelt

Regenerate the editor schema as UTF-8 and review the
[upstream rule catalog](https://github.com/mathisarends/smelt/blob/v0.5.0/docs/rules/README.md)
and [upgrade guide](https://github.com/mathisarends/smelt/blob/v0.5.0/docs/upgrading.md#040-to-050).
Run the full policy and inspect new findings before adopting rules.
Keep suppressions and exclusions tied to actual contracts.

See [contributing](contributing.md) for the remaining checks.
