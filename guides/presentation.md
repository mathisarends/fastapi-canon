# Presentation

The presentation layer is the HTTP adapter. It owns FastAPI routers and dependencies, Pydantic transport schemas, HTTP mappings, cookies, and exception handlers. It does not query SQL or construct concrete infrastructure adapters.

For layer direction, see [Architecture](architecture.md). For practical examples, see the reference task [`router.py`](../examples/reference-service/src/app/features/tasks/presentation/router.py), [`schemas.py`](../examples/reference-service/src/app/features/tasks/presentation/schemas.py), and [`mapper.py`](../examples/reference-service/src/app/features/tasks/presentation/mapper.py).

## Routers

Keep endpoints linear: accept validated input, call application services, map the result, and return. Business decisions belong to domain/application code.

Assign service results to a named local before mapping them. Do not inline an
awaited service call inside a mapper or build collection response schemas in
the endpoint. Put collection conversion in a named presentation mapper:

```python
async def list_tasks(
    service: FromDishka[TaskService],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> TaskListResponse:
    tasks = await service.list_recent(limit=limit)
    return to_list_response(tasks)
```

Every route decorator must declare:

- a stable, globally unique `operation_id`;
- an explicit success `status_code`;
- a named `response_model` (or explicit `None` for a bodyless response);
- relevant non-success responses with named schemas; and
- a feature tag and router prefix.

Treat `operation_id` as a public client-generation identifier. Prefer predictable verb-and-resource names such as `create_task`, `list_tasks`, and `complete_task`. Do not derive it from Python function names implicitly or change it during internal refactors.

## Schemas

Define separate request and response models even when their fields currently match. Name schemas after their transport role: `CreateTaskRequest`, `TaskResponse`, `TaskListResponse`, and `ErrorResponse`.

Do not use SQLModel table types or domain entities as FastAPI schemas. Do not expose untyped `dict` bodies. Prefer an explicit envelope schema for collections when pagination or metadata may evolve:

```python
class TaskListResponse(Schema):
    items: list[TaskResponse]
```

Centralize deliberate Pydantic defaults—alias behavior, unknown-field policy, and attribute parsing—in one presentation base. Add field bounds and descriptions where they form part of the API contract. Do not silently inherit database nullability as API optionality.

## Mapping

Map domain/application outputs explicitly to response schemas. Keep non-trivial mapping, including collection comprehensions, in a named mapper function rather than the endpoint or ORM adapter. This makes public API evolution independent from domain and storage changes.

For the corresponding persistence boundary, see [Infrastructure](infrastructure.md#mapping).

## Dependencies

Use `FromDishka` for application services. Use FastAPI `Depends` for HTTP-derived values such as authentication, cookies, headers, and request context. Framework dependency types do not cross into application methods.

For scopes and provider composition, see [Dependency Injection](dependency_injection.md). For the authenticated-principal pattern, see [Authentication](authentication.md#layer-boundaries).

## Errors

Register exception handlers at the feature boundary. Map known domain/application exceptions to consistent named error schemas and declare those responses on every endpoint that can produce them.

Use `401` for absent/invalid authentication, `403` for denied authorization, `404` when a resource is absent or intentionally undisclosed, and `409` for a state conflict. Do not leak stack traces, credentials, database constraint text, or provider payloads.

Make validation-error shape an intentional API contract. If the framework default is retained, document it consistently; if clients require a canonical error envelope, install one validation exception handler and describe its schema globally.
