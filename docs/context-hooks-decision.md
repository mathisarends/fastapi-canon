# Context hook API decision

Decision date: 2026-11-05.

Status: API direction accepted. Implementation resumed after updating the
isolated worktree to origin/main on 2026-11-05. The feature is not published.

## Accepted developer API

Register ordinary functions in a tuple on the router:

```python
routes = CanonWebSocketRouter(
    context=screencast_connection,
    resolver=Dishka(),
    hooks=(authorized_project,),
)
```

An `authorized_project` function returning `AuthorizedProject` supplies
`Inject[AuthorizedProject]`. The return annotation supplies the registration key;
applications do not repeat the type alongside the function. Hook dependencies
determine execution order; tuple order does not describe a processing pipeline.
Functions remain independent of a particular router, and tuples can be reused.

`Inject[T]` requests either a registered hook result or an application dependency
from the configured resolver. A registered hook is authoritative for its type;
hook failures must propagate without falling back to DI. Duplicate result types
and dependency cycles must produce configuration errors.

Hooks can consume ordinary FastAPI route parameters and dependencies alongside
`Inject[T]` parameters. Dishka providers retain responsibility for application
objects, resource lifetimes, caching and cleanup.

## Scope ownership

For WebSocket admission, Canon opens a short REQUEST scope for the complete
context preparation and closes it before acceptance. Provider declarations own
the dependency scope; hooks do not repeat a scope setting. Resources needed for
the connection lifetime belong in SESSION scope. Values passed out of admission
must remain usable after its REQUEST scope has closed.

Do not add a Canon UnitOfWork abstraction, an InUnitOfWork annotation, a Hook
configuration wrapper or a separate resource decorator. Generator providers
already express resource acquisition and cleanup in Dishka.

## Shared router integration

The shared `APIRouter.include_channel(...)` forwards `context=`, `resolver=` and
`hooks=` to the WebSocket adapter. Applications can use that entry point for
JSON-RPC channels while retaining specialized endpoints for binary streams and
raw sockets. The admission graph and resource boundaries are the same for both
registration styles. Integration tests cover hook reuse, route/header inputs,
independent connections and cleanup before acceptance through the shared router.
