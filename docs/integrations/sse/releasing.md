# Releasing Canon SSE

Use the [Canon release process](../../releases.md). Only root
workflows execute in this repository. Version metadata and release validation
come from the root `release.toml` and `scripts/release.py`.

SSE ships in the single `fastapi-canon` distribution and shares its version,
root lockfile, [changelog](../../../CHANGELOG.md) and
`fastapi-canon-vX.Y.Z` tags. It is not a separate release target. Historical
standalone releases remain in the [predecessor changelog](../../history/fastapi-streams.md)
and the locations recorded in [repository migration](../../repository-migration.md#release-evidence).

Run development and release-preparation commands from the repository root.
Prepare a new Canon version, update the changelog and generated clients, pass the
repository and installed-artifact checks, and wait for CI on the exact commit.
Run the root release workflow with `dry_run=true` before publication. A real
publication requires explicit authorization.

[Back to documentation](index.md)
