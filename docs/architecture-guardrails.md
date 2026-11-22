# Architecture and test guardrails

[smelt.yml](../smelt.yml) defines dependency boundaries, source conventions and
test policies. The development lockfile pins `smelt-cli==0.16.1`.
[.smelt.schema.json](../.smelt.schema.json) provides editor completion.

## Run the policy

```console
uv run --frozen smelt context fastapi_canon.error.registry
uv run --frozen smelt check --config smelt.yml --changed --fail-on warning
uv run --frozen smelt check --config smelt.yml --fail-on warning
uv run --frozen smelt explain TST005
```

The full check runs in CI, `scripts/check.py all` and pre-commit.
The gate fails on warnings and errors. Use `--show-coverage` for the source
inventory and `--show-hints` for design reviews such as facade ownership,
discriminators, provider registrations and callable operations.
Changed-file checks still enforce complete coverage.

Keep application factories at the deployment entry point. Put reusable logic
in modules that can be imported without bootstrapping the app.
Review modules with more than 20 direct project imports, complex comprehensions,
constructor branches or many private definitions. Name awaited values before
passing them as arguments.

Keep library `__all__` lists in package facades (`__init__.py`). Omit module
docstrings; document classes and functions where their names and types do not
explain the public contract.

The visibility rules check implementation functions, constants and attributes,
including APIs used only by tests. Private literal class constants belong at
module scope. Repository-owned YAML uses `.yml`, and Compose files use
`compose` as their basename. pnpm and the agent skill retain their required
filenames.

Declare `StrEnum` members with explicit string literals. Their wire and public
values must remain visible and independent of Python member names; do not use
`auto()` for these enums.

Name tuple fields with a `NamedTuple` or frozen dataclass, and make parameters
after the third keyword-only. Framework callbacks and public signatures keep
their shape with a reasoned suppression.

Inspect falsy inputs and dependency lifetimes before changing behavior. Record
breaking API changes with migration steps in the changelog.

Explain broad exception recovery at the handler. Name numeric durations with
their unit and repeated waits with their purpose. Review mutable fields in
frozen records, parallel sequence fields and repeated immutable lookup sets.
Use explicit branches for nested conditional expressions. Reference-service
schemas and database models inherit its configured project bases.

Dataclasses hold values: require `frozen` and `slots`, with `kw_only` for codegen
configuration and result objects. Records with cached properties retain their
instance dictionary. Keep accumulation, caches and live lifecycle state in
ordinary classes; return immutable snapshots when reporting completed work.

## Change the policy

Use Smelt for architecture and source conventions. Use Ruff for lint and
formatting. Read [architecture](architecture.md) before changing package roles
or test structure.

Fix a finding or document a narrow, concrete exception beside the affected code.
Do not add debt baselines, broad exclusions or weaker rules to pass checks.
Unused suppressions fail. Shared exports must have a consumer contract.

Tests must assert observable outcomes, use bounded waits and subprocesses, and
avoid private production access. Integration and contract tests prohibit mocks;
unit tests normally allow one object-targeted patch. The configuration records
the few existing exceptions and their reasons.

Tests that mirror a Canon module may import that package's internals. All other
tests use documented imports.

## Upgrade Smelt

Regenerate the editor schema as UTF-8 and review the
[upstream rule catalog](https://github.com/mathisarends/smelt/blob/v0.16.1/docs/rules/README.md)
and [upgrade guide](https://github.com/mathisarends/smelt/blob/v0.16.1/docs/upgrading.md).
Run the full policy and inspect new findings before adopting rules.
Keep suppressions and exclusions tied to actual contracts.

The official Smelt skill and offline rule references for the pinned release are
checked in under `.agents/skills/smelt`. Refresh them with every Smelt upgrade:

```console
uv run --frozen smelt skill install --force
```

Do not edit the installed skill; keep Canon-specific policy in `smelt.yml` and
repository instructions.

See [contributing](contributing.md) for the remaining checks.
