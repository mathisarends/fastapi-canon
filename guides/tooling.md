# Tooling

## Project shape

Use a `src/` layout and install the package for development. Keep application imports absolute and independent of the working directory.

Declare runtime dependencies in `[project.dependencies]` and development tools in a development dependency group. Commit `uv.lock` for deployable services.

For repositories containing multiple Python distributions, follow [Workspace](workspace.md). Do not introduce a workspace merely to represent feature modules inside one deployable service.

The reference stack is:

- FastAPI and Uvicorn for HTTP/ASGI
- Pydantic and pydantic-settings for transport schemas and configuration
- Dishka for application dependency injection
- SQLModel on SQLAlchemy's async session API for persistence
- Alembic for migrations
- Ruff and mypy for static verification

## Commands

Expose a small stable command set in documentation and CI:

```console
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

Use the same commands locally and in CI. Do not hide materially different behavior in editor-only configuration.

## Configuration

Use `BaseSettings` classes grouped by infrastructure concern. Environment variables are deployment inputs; `.env.example` documents names but contains no credentials.

## CI

Run lint, type checks, and builds from the locked dependency graph. Pin infrastructure service versions used by CI.

Container image and Compose conventions are defined in [Docker and Compose](docker.md).
