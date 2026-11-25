# 2. Errors

Declare a public error once and use it on every transport. Handlers raise plain
exceptions; Canon renders [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html)
Problem Details and documents them.

## Define an error

```python
from fastapi_canon import Error


class ProjectMissing(Exception):
    pass


project_missing = Error(
    ProjectMissing,
    status=404,
    code="project_missing",
    detail="Choose another project.",
)
```

`title` defaults to the code in words (`Project missing`); pass `title=` to
override it. Exception messages never reach the client: only `detail` and
extensions are public.

## Add fields

Static fields go in `extensions=`. For per-occurrence fields, return a Pydantic
model from a renderer; it becomes the documented schema:

```python
class RetryFields(BaseModel):
    retry_after: int


def retry_fields(error: RateLimited) -> RetryFields:
    return RetryFields(retry_after=error.seconds)


rate_limited = Error(
    RateLimited,
    status=429,
    code="rate_limited",
    extensions=retry_fields,
    headers={"Retry-After": "60"},
)
```

Static `headers=` are documented in OpenAPI automatically.

## Declare where it can happen

```python
@router.get("/projects/{project_id}", raises=[project_missing])
async def get_project(project_id: int) -> Project: ...


@router.get(
    "/projects/{project_id}/events",
    response_class=EventSourceResponse,
    raises=[project_missing],
)
async def events(project_id: int) -> AsyncIterator[ProjectEvent]: ...


@projects.method(raises=[project_missing])
async def cancel(project_id: int) -> None: ...
```

`raises=` both registers the error and documents it on that operation. Put it on
an `APIRouter` or a [`RouterPolicy`](05-composition.md#router-policies) to share it.
Errors that no operation declares, such as global dependency failures, go in
`Feature(errors=[...])`.

Set the problem type prefix once:

```python
Composition(
    features=..., errors=ErrorOptions(type_base="https://api.example.com/problems")
)
```

## What the client receives

| Where it fails | What is sent |
| --- | --- |
| HTTP route, or SSE before the first event | `application/problem+json` response |
| SSE after the stream started | a terminal `canon.problem` event |
| RPC method | JSON-RPC error with the problem in `error.data.problem` |

Generated Python and TypeScript clients raise `ProblemError`, with a subclass per
known error such as `ProjectMissingError`. Branch on the error class or the
problem `type`, never on `title` or `detail` text.

## Advanced

Custom transports read the installed catalog with
`fastapi_canon.error.installed_catalog(app)`; see the
[public API](21-public-api.md). A runnable comparison of all three transports is
in [`examples/canonical_errors`](../examples/canonical_errors/app.py).

---

Previous: [1. Quickstart](01-quickstart.md) · [Learning path](README.md) · Next: [3. Streaming](03-streaming.md)
