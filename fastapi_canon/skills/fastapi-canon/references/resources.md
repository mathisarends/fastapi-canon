# Resources

`fastapi_canon.integrations.database` (`sqlite`/`postgres` + `migrations` extras):

```python
database = Database(url=settings.database_url, migrations=AlembicMigrations(...))
Feature(name="projects", ..., integrations=[Tables(ProjectRow)])
Composition(features=..., integrations=[database])
```

- Each request is one transaction: commit on success, rollback when an
  exception escapes. Repositories inject `AsyncSession` and never commit.
- Streams, sockets and tasks outlive the request. Give them a `Scoped[T]`
  (one transaction per call) or `ServiceScope[T]` factory, bound at APP scope.
- Migrations: `fastapi-canon db revision --autogenerate`, `db upgrade head`.

Guide: [database](https://github.com/mathisarends/fastapi-canon/blob/main/docs/09-database.md).
