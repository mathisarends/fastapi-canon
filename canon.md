# FastAPI Canon

## Structure

Organize business code by feature. Within a feature, separate `domain`, `application`, `infrastructure`, and `presentation` when those layers contain real behavior.

Dependencies point inward: presentation and infrastructure may depend on application/domain; domain must not import FastAPI, Pydantic, Dishka, SQLAlchemy, or SQLModel.

Expose each feature through one descriptor containing its routers, dependency providers, and optional exception-handler registration. Assemble feature descriptors only in the application composition root.

Treat `__init__.py` as a curated public API. Re-export only stable symbols intended for consumers, use explicit relative imports, declare them in `__all__`, and never use wildcard exports. Reserve relative imports for `__init__.py`; use absolute imports in regular modules. Consumers use the public facade; implementations import owned ABCs from their definition module directly.

## Domain and application

Model domain state and rules with plain Python types.

Model entities and aggregates as normal classes. Share identity and creation
metadata through a small framework-free `Entity` base; use `Aggregate = Entity`
as semantic sugar for aggregate roots. Reserve frozen dataclasses for value
objects and application result records.

Keep entity state private and expose only required reads through getter-only
properties. Mutate protected state through named domain methods. Use a public
attribute only when unrestricted direct reading and writing is intentional.

Define persistence contracts beside the domain that needs them. Make contracts use domain language and domain types, not generic query APIs or ORM models.

Prefer `ABC` and `@abstractmethod` for interfaces owned by the application. Concrete implementations must import and explicitly inherit the ABC. Use `Protocol` only when structural typing is intentional, especially for third-party or caller-owned shapes.

Inject repository contracts and other ports through service constructors. Application services must not use FastAPI `Depends` or resolve dependencies themselves.

Construct domain objects as named local variables before passing them to repositories or other collaborators. Do not hide meaningful object creation inline inside a dependency call.

Raise application/domain exceptions from services. Translate them to HTTP responses in presentation exception handlers.

## Dependency injection

Use Dishka providers as the composition root for non-HTTP dependencies. Scope stateful database sessions, repositories, and services to a request; scope configuration, engines, and stateless clients to the application.

Use FastAPI `Depends` only for HTTP concerns such as authentication, headers, cookies, and request-derived values. Do not let `Depends`, `Request`, or `HTTPException` leak into application or domain code.

## Persistence

Use one `AsyncSession` transaction per HTTP request. Commit after successful request dependency execution; roll back on exceptions.

Repositories receive an existing session. They may `flush` to obtain database-generated values but must not commit.

Keep domain entities, SQLModel table models, and Pydantic API schemas distinct. Map explicitly at layer boundaries.

Keep table models in one central ORM module while that remains navigable. Co-locate them with feature infrastructure only after real size or cohesion pressure, and always register every table explicitly for schema tooling.

Use a typed generic SQL repository only for mechanics that are truly identical across aggregates. Put meaningful queries and operations on feature-specific repository contracts and implementations.

Use Alembic migrations for schema changes. Do not call `metadata.create_all()` during production startup.

## HTTP API

Keep routers thin: validate transport input, call one or more application services, map results, and select HTTP status codes. Assign service results to named locals before mapping; do not inline awaited service calls or collection mapping in endpoint returns.

Use separate request and response schemas. Centralize shared Pydantic configuration in a presentation base schema.

Give every endpoint a stable, globally unique `operation_id`, an explicit success status, and named request, response, and relevant error schemas.

Do not return ORM objects from endpoints.

## Authentication

Treat authentication, session management, and authorization as separate concerns. Authentication establishes a principal; every protected use case must still enforce authorization.

Extract and validate credentials in presentation dependencies. Expose a typed principal such as `AuthenticatedUserId` to endpoints; do not pass raw tokens into application services.

Keep external provider identities separate from local users. Link them by stable provider and subject identifiers, never by an unverified email address alone.

For OAuth/OIDC, use Authorization Code with PKCE and bind callbacks to a short-lived, one-time `state`. Use exact registered redirect URIs.

Validate token signature and an explicit algorithm plus issuer, audience, expiry, and token purpose. Never accept an access token where a refresh token is required or vice versa.

For browser sessions, prefer `Secure`, `HttpOnly`, explicitly scoped `SameSite` cookies. Protect every cookie-authenticated state-changing request against CSRF. Rotate or sender-constrain refresh tokens and revoke the session family when replay is detected.

Keep credentials and tokens out of URLs, logs, exception text, analytics, and persistent browser storage.

## Configuration and lifecycle

Load typed settings through `pydantic-settings`. Instantiate settings at the composition root, not at import sites throughout the codebase.

Create external resources through application-scoped providers and close them in FastAPI lifespan shutdown.

## Tooling

Use a `src/` layout, `pyproject.toml`, uv, Ruff, and mypy. Keep runtime and development dependencies separate.

Use a uv workspace when multiple independently packaged Python components share one repository and must be developed or released together. Keep a single package when boundaries are only internal modules.

Keep `compose.yml` at the repository root and each Dockerfile beside the package or process it describes.

Run formatting, linting, type checking, and builds in CI from a locked dependency graph.
