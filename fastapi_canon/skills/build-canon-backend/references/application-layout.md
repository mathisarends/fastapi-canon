# Application layout and ownership

Use this when creating a consumer backend or deciding where a change belongs.
The target is feature-first, with inward dependencies inside each feature.

## Target tree

`backend/` is a deployment/project directory; `backend/src/backend/` is the Python
package. A standalone service can use `src/<package>/` directly. The layout does
not require a workspace, a frontend, or every illustrated module.

```text
backend/
  pyproject.toml
  migrations/                    Application-owned Alembic history
  src/backend/
    __init__.py
    main.py                      Server entrypoint
    app.py                       create_app; applies Composition
    composition.py               Explicit ordered feature tuple
    settings.py                  Application configuration
    features/
      __init__.py
      projects/
        __init__.py
        feature.py               Canon Feature; assembly outside the layers
        domain/
          __init__.py             Public domain vocabulary
          projects.py             Entities, values, invariants, plain exceptions
        application/
          __init__.py             Public application API
          projects.py             Related workflows / injectable service
          ports.py                Owned dependency interfaces, if substantial
          models.py               Application records, if needed
        infrastructure/
          __init__.py
          di.py                   Dishka bindings; assembly outside the layers
          repository.py           SQL implementation of the inward port
          orm.py                  Persistence schema
        presentation/
          __init__.py
          router.py               CanonRouter and thin HTTP handlers
          schemas.py              Request/response models
          mapper.py               Wire mapping when it warrants a module
          errors.py               Error values and ErrorRegistry
    platform/                    Shared concrete runtime capabilities
      database/                  Database declaration and metadata assembly
      http/                      Owned client pools
    shared/                      Small framework-free vocabulary used by features
  tests/
    unit/features/projects/application/test_projects.py
    integration/features/projects/infrastructure/test_repository.py
    integration/features/projects/presentation/test_router.py
    contract/features/projects/presentation/test_router.py
```

Add `domain/` when there are business rules or domain vocabulary. A simple
application record and workflow may live entirely in `application/`; do not
invent aggregates, base repositories or empty layers to fill the tree. A tiny
health feature may only need a router and `feature.py`.

## Dependency decisions

| Owner | Owns | May consume |
| --- | --- | --- |
| Domain | Business identity, invariants, state transitions, plain exceptions | Standard library; its own domain vocabulary |
| Application | Workflows, use cases, application records, required I/O ports | Domain; Pydantic for application data when useful |
| Infrastructure | SQL, external HTTP, files, concrete implementations | Application and domain contracts |
| Presentation | HTTP/SSE/RPC DTOs, auth prerequisites, wire mapping and errors | Application API and needed domain vocabulary |
| Wiring | Providers, feature contributions, complete app assembly | The layers being assembled |

An ORM row is not a domain entity or public response. A public `Error` is not a
business exception: its status and problem URI belong to presentation. Keep
FastAPI, Dishka, Canon and SQLAlchemy out of domain/application behavior.

Put a port beside the layer that needs it. Prefer `ABC`/`abstractmethod` for
application-owned injected repositories and gateways, with implementations
inheriting the port. A domain repository interface can stay in domain when the
existing aggregate design owns it. Preserve existing sound interface contracts.

Keep application services as ordinary classes with explicit typed constructors
and `_role_name` implementation attributes. Dataclasses suit data records and
values. Group related operations in flat subject modules (`projects.py`,
`uploads.py`, `preferences.py`). Separate an independently injectable callable
use case when its workflow, dependencies or lifetime warrants that boundary.

Export intentional inter-package contracts from the owning `__init__.py` and
import that facade externally. Within the same package, import the defining
module to avoid a circular dependency through its facade. Keep implementation
helpers private within a descriptively named module.

Features cooperate through explicit application contracts. Put legitimate
cross-feature access in the project's architecture policy; do not reach into a
foreign SQL repository or presentation module. Treat `platform` as shared runtime
adapters and `shared` as a small inward vocabulary, rather than a home for
unrelated behavior. A new abstraction needs concrete use.

See the [reference service](https://github.com/mathisarends/fastapi_canon/tree/fastapi-canon-v0.13.0/examples/reference-service/src/app)
for a complete example.
