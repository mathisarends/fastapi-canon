# Optional error-contract checking

Run the source checker to find application exceptions missing from the
`raises=` declarations of `APIRouter` operations, SSE streams, WebRTC sessions
and `RpcChannel` methods:

```console
fastapi-canon check errors src --strict
```

Pass Python import roots: for `src/app/features/...`, use `src`, not `src/app`.
Pass shared application libraries as additional roots. The checker parses source
without importing the application, so it needs no annotations or running services.
Run it in the application's environment so that installed dependencies can be
located as libraries.

A `raises` declaration documents an operation's allowed errors; it does not catch
anything. Runtime mapping still requires the `Error` to be registered, so keep
HTTP tests for important Problem Details responses.

## Findings and CI

```text
service.py:24: CAN001 [app.router.complete_task] app.service.TaskNotFound: app.errors.task_not_found is missing from raises
  app.router.complete_task -> app.service.TaskService.complete
```

| Code | Meaning |
| --- | --- |
| `CAN001` | An application exception is mapped in the catalog but missing from the operation's combined `raises`. |
| `CAN002` | An application exception has no mapping in `raises` or the catalog. |
| `CAN003` | A call or declaration could not be resolved; see [analysis boundaries](#analysis-boundaries). |

The catalog is every `Error` referenced by a `raises=` or `errors=` keyword in the
scanned source. Policy, router and operation `raises` combine.

Default mode reports and exits zero. `--strict` exits one for CAN001/CAN002;
`--require-complete` independently exits one for CAN003 or a scan without
supported operations. Invalid roots, duplicate modules and syntax errors exit two.
The [quickstart](01-quickstart.md#check-the-contract-in-ci) shows a minimal CI setup.

Text output ends with the root causes of incomplete analysis, ranked by the number
of affected operations; fix the top entries first. `--format json` reports each
operation's `transport`, `diagnostics` and `complete` flag, plus `contract_gaps`,
`incomplete_operations` and `root_causes` (`path`, `line`, `message`,
`operations`). The JSON fields are the machine-readable interface; the `checks`
modules are private.

## Transports

| Declaration | Compared with |
| --- | --- |
| `@router.get(...)` and other HTTP methods | Endpoint and dependency failures against policy, router and operation `raises=`. |
| `@router.sse(...)` | Dependency and stream body failures against router and stream `raises=`. |
| `@rtc.session(...)` on a `WebRtcRouter` | Session factory and dependency failures against router and session `raises=`. |
| `@channel.method` on an `RpcChannel` | Method failures against the channel and method `raises=`. |

Unconditional module-level `parent.include_router(child)` calls pass a parent's
`raises`, dependencies and `RouterPolicy` to its children. Plain FastAPI routers
are skipped.

## How the check works

For each operation, the checker collects the application exceptions that can
escape its endpoint, dependencies and everything they call, then compares them
with the declared errors. It over-approximates: every branch and loop may run, and
when a call has several possible targets, all of them contribute. A finding is a
possible path, not proof that a request reaches it.

It relies on these closed-world assumptions:

- **Libraries raise no application exceptions.** Code outside the roots reaches
  application code only through what it receives: callables passed as arguments,
  overridden methods of library bases, and construction hooks and decorated
  methods (such as Pydantic validators) of classes with library bases.
  Decorators from libraries are transparent.
- **The roots contain every application class.** An untyped receiver whose method
  name no application class defines is a library value. An abstract method,
  property or `Protocol` member without a Dishka binding follows every scanned
  implementation.
- **Annotations are true.** Parameters, fields, properties and return values have
  their annotated types; iterating a `list[T]`, `tuple[T, ...]` or `dict[T, V]`
  yields `T`. An `HTTPConnection` injected into an HTTP operation is a `Request`,
  also in the helpers it is passed to.
- **Handlers catch by inheritance.** `except LibraryError` catches application
  exceptions that subclass it and nothing else.
- **Deferred bodies run later.** Errors of generator expressions and unawaited
  coroutines are reported, but no handler in the creating function catches them.

Implicit interpreter errors, framework internals, startup and background tasks are
outside the comparison, as are built-in and third-party exceptions.

### Dishka ports

Calls to a port follow the implementations that `Provider` classes in the roots
bind to it, without constructing a container:

```python
class TaskProvider(Provider):
    # The return annotation names the port; the returned value names the
    # implementation. A returned parameter binds that parameter's type.
    @provide(scope=Scope.REQUEST)
    def task_repository(self, session: AsyncSession) -> TaskRepository:
        return SqlTaskRepository(session)

    verifier = provide(LocalTokenVerifier, provides=TokenVerifier, scope=Scope.APP)
```

Several bindings of one port, including `override=True` and test providers,
contribute the union of their errors.

### RPC channel subclasses

Methods of statically resolved `RpcChannel` subclasses are checked like methods of
`RpcChannel` itself. A supported subclass has one base, no type arguments, class
decorators or metaclass keywords, and a body of only `pass` or a docstring.

### Direct local route factories

A synchronous, undecorated factory without parameters may declare local routers or
channels, their operations and helpers, and return them. The checker indexes these
declarations without calling the factory; nested operations get names such as
`app.create_router.<locals>.endpoint`.

## Analysis boundaries

These cases stay `CAN003`; the message names what to fix:

- **Ambiguous calls:** a method that application classes define, called on a
  receiver of unknown type. The message lists candidates; annotate the receiver.
- **Dynamic dispatch:** `getattr` with a computed name, calling the result of
  `eval`, `exec`, `compile` or `__import__`, unresolved `super()` calls, and
  raising a value of unknown type.
- **Application decorators:** a decorator defined in the roots can wrap a call
  arbitrarily.
- **Transport narrowing:** a `WebSocketException` that may escape an HTTP
  operation through a connection of unknown origin.
- **Unlocated code:** imports that are neither scanned nor installed, or packages
  that contain a scanned root during a partial scan.
- **Ports without implementations:** an abstract method that no scanned class
  implements.
- **Dynamic contracts:** unresolved `raises`/`errors` sequences, reassigned or
  mutated declarations, conditional or repeated mounts with inherited contracts,
  `RpcError` subclasses, `RpcErrorBinding` and RPC subclasses with custom logic.
- **Unsupported route factories:** parameters, conditional setup, decorators,
  async or generator factories.

Measure the checker on your own runner with
`uv run --locked python scripts/benchmark_error_contracts.py --output dist/checker-benchmark.json`.

---

Previous: [Client generation](06-codegen.md) · [Learning path](README.md) · Next: [File transfers](08-file-transfers.md)
