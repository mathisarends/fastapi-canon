# Application layout

Feature-first folders with inward dependencies inside each feature. Create only
modules with real responsibilities.

```text
src/backend/
  app.py                     create_app; builds the Composition
  composition.py             Ordered feature tuple
  settings.py
  features/projects/
    feature.py               Feature(...) — assembly, outside the layers
    domain/projects.py       Entities, invariants, plain exceptions
    application/projects.py  Workflows/services and the ports they need
    infrastructure/
      di.py                  Dishka Provider — assembly
      repository.py          Implements the application port
      orm.py                 Persistence schema
    presentation/
      router.py              APIRouter and thin handlers
      schemas.py             Request/response models
      errors.py              Public Error definitions
  platform/                  Shared runtime adapters (database, HTTP pools)
  shared/                    Small framework-free vocabulary
tests/{unit,integration,contract}/features/projects/...
```

Skip `domain/` when there are no business rules; a health feature may need only
a router and `feature.py`. No empty layers, base repositories or `services/`
folders — group related operations in subject modules.

| Layer | Owns | May import |
| --- | --- | --- |
| Domain | Identity, invariants, plain exceptions | Standard library |
| Application | Workflows, records, ports (`ABC`) | Domain, Pydantic |
| Infrastructure | SQL, external HTTP, files | Application, domain |
| Presentation | DTOs, auth prerequisites, wire mapping, `Error`s | Application, domain |
| Wiring | Providers, `Feature`, app assembly | Everything it assembles |

- Keep FastAPI, Dishka, Canon and SQLAlchemy out of domain and application.
  An ORM row is neither a domain entity nor a response; an `Error` is not a
  business exception.
- Services are plain classes with typed constructors receiving ports.
- Export cross-package contracts from the owning `__init__.py`. Features talk
  through application contracts, never another feature's repository or router.

Full example: the [reference service](https://github.com/mathisarends/fastapi-canon/tree/main/examples/reference-service/src/app).
