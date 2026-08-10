# FastAPI Canon

Opinionated, reusable conventions for feature-oriented FastAPI services.

- [`canon.md`](canon.md) is the short normative reference.
- [`guides/architecture.md`](guides/architecture.md) defines feature slices and layer boundaries.
- [`guides/domain.md`](guides/domain.md) defines entity, aggregate, and value-object conventions.
- [`guides/application.md`](guides/application.md) defines use-case and application-service conventions.
- [`guides/dependency_injection.md`](guides/dependency_injection.md) defines constructor injection, provider composition, and FastAPI boundaries.
- [`guides/database.md`](guides/database.md) defines SQLModel, transaction, and repository conventions.
- [`guides/migrations.md`](guides/migrations.md) defines the preferred Alembic ownership and file structure.
- [`guides/infrastructure.md`](guides/infrastructure.md) defines adapter, configuration, and resource conventions.
- [`guides/imports-and-reexports.md`](guides/imports-and-reexports.md) defines package APIs, `__init__.py` facades, and cross-package imports.
- [`guides/presentation.md`](guides/presentation.md) defines FastAPI router, schema, mapping, and error conventions.
- [`guides/authentication.md`](guides/authentication.md) defines identity, OAuth, token, cookie, and authorization conventions.
- [`guides/uv-workspace.md`](guides/uv-workspace.md) records the repository's uv workspace opinions.
- [`guides/docker.md`](guides/docker.md) shows the preferred Docker file structure.
- [`examples/reference-service/`](examples/reference-service/) demonstrates the rules in a small authenticated service.

The canon is distilled from production code. It intentionally excludes product-specific behavior.
