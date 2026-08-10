# Docker and Compose

Docker defines a deployable process image. Docker Compose defines a reproducible multi-process topology for local development and production-like infrastructure. These conventions apply to single-package repositories and workspaces alike.

## Images

Build from the committed lockfile. Separate build and runtime stages, copy only runtime artifacts into the final stage, and run as a non-root user. Keep the image deterministic and free of developer credentials, caches, and bind-mounted source.

Use exec-form commands so signals reach the ASGI server. Configure graceful shutdown and expose an application health endpoint. Do not run database migrations concurrently in every API replica.

For a uv workspace, use the repository root as build context so the root lockfile and local member packages are available. Keep one Dockerfile per independently deployed member when runtime contents differ.

## Compose role

Use `compose.yml` to define the local topology: database, broker, object-store emulator, API, and workers. Compose is useful when behavior crosses a process or depends on production-like infrastructure. Compose is not the production deployment specification.

Prefer a small base file. Put optional observability tools, emulators, and other expensive services behind profiles. A development-only override may add source bind mounts and reload commands.

```yaml
services:
  db:
    image: postgres:17
    environment:
      POSTGRES_DB: app
      POSTGRES_USER: app
      POSTGRES_PASSWORD: local-only
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 2s
      timeout: 3s
      retries: 20
    volumes:
      - postgres-data:/var/lib/postgresql/data

  migrate:
    build:
      context: .
      dockerfile: services/api/Dockerfile
    command: ["uv", "run", "alembic", "upgrade", "head"]
    environment:
      DATABASE_URL: postgresql+asyncpg://app:local-only@db/app
    depends_on:
      db:
        condition: service_healthy

  api:
    build:
      context: .
      dockerfile: services/api/Dockerfile
    environment:
      DATABASE_URL: postgresql+asyncpg://app:local-only@db/app
    depends_on:
      migrate:
        condition: service_completed_successfully
    ports:
      - "8000:8000"

volumes:
  postgres-data:
```

## Readiness and migrations

Treat startup order and readiness separately. `depends_on` without a health or completion condition only orders container startup. Give stateful dependencies real health checks and make application health checks reflect whether the process can serve traffic.

Run migrations as an explicit one-shot service after the database becomes healthy and before application processes start. Make repeated migration execution safe, and fail startup when migration fails.

## Data, configuration, and secrets

Use named volumes for persistent developer data. Use tmpfs, disposable volumes, or unique Compose project names for isolated CI runs. Document how to reset local state without making reset part of ordinary startup.

Store only non-secret local defaults in Compose. Inject deployed credentials through the environment or a secret manager. Do not bake `.env`, cloud credentials, private package tokens, or production certificates into an image.

Use service DNS names such as `db` between containers; `localhost` refers to the current container. Publish only ports local consumers need on the host.

## Verification

Build images in CI from a clean checkout and locked dependencies. Start the smallest required Compose profile, wait for health rather than sleeping a fixed interval, run migrations, and always collect service logs on failure.
