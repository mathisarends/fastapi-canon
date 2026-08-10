# FastAPI Canon

Opinionated, reusable conventions for feature-oriented FastAPI services.

- [`canon.md`](canon.md) is the short normative reference.
- [`guides/architecture.md`](guides/architecture.md) defines feature slices and layer boundaries.
- [`guides/domain.md`](guides/domain.md) defines entity, aggregate, and value-object conventions.
- [`guides/application.md`](guides/application.md) defines use-case and application-service conventions.
- [`guides/dependency_injection.md`](guides/dependency_injection.md) defines constructor injection, Dishka scopes, and FastAPI boundaries.
- [`guides/database.md`](guides/database.md) defines SQLModel, transaction, and repository conventions.
- [`guides/infrastructure.md`](guides/infrastructure.md) defines adapter, configuration, and resource conventions.
- [`guides/imports-and-reexports.md`](guides/imports-and-reexports.md) defines package APIs, `__init__.py` facades, and cross-package imports.
- [`guides/presentation.md`](guides/presentation.md) defines FastAPI router, schema, mapping, and error conventions.
- [`guides/authentication.md`](guides/authentication.md) defines identity, OAuth, token, cookie, and authorization conventions.
- [`guides/workspace.md`](guides/workspace.md) explains when and how to use a uv workspace.
- [`guides/docker.md`](guides/docker.md) defines image and Docker Compose conventions.
- [`guides/tooling.md`](guides/tooling.md) defines the remaining development and CI toolchain.
- [`examples/reference-service/`](examples/reference-service/) demonstrates the rules in a small service.

The canon is distilled from production code. It intentionally excludes product-specific behavior.
