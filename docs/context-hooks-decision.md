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

## Resume after upstream merges

Refresh origin/main and reconcile the local prototype with the merged router,
transport and composition APIs before completing implementation. Revisit the
integration points against that source rather than relying on earlier API
sketches. Complete documentation and the required verification before opening
the requested pull request.
