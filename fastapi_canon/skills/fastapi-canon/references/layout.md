# Layout

```text
src/backend/
  app.py                       Composition + create_app
  features/projects/
    feature.py                 Feature(...)
    domain/                    Rules, plain exceptions
    application/               Services and the ports (ABC) they need
    infrastructure/            Port implementations, di.py (Dishka Provider)
    presentation/              router.py, schemas.py, errors.py
```

Dependencies point inward: domain ← application ← infrastructure/presentation.
Keep FastAPI, Dishka and Canon out of domain and application. Create only the
layers a feature needs. Features talk through application contracts.

Full example: [reference service](https://github.com/mathisarends/fastapi-canon/tree/main/examples/reference-service/src/app).
