# Check application error contracts

Run the source checker over your application and shared application libraries:

```console
fastapi-canon check errors src libs/orders/src --strict --require-complete
```

Pass Python import roots: for `src/app/features/...`, use `src`, not `src/app`.
Each root must contain distinct module names. Run in the application's Python
environment so installed dependencies can be located. The equivalent module
command is `python -m fastapi_canon.check_error_contracts`.

The checker finds **application-defined exceptions escaping an operation** and
compares them with that operation's `raises=` contract. It parses source without
importing the inspected application, constructing a Dishka container, opening a
database or starting a browser. It needs no annotations specific to the checker.

```python
from fastapi_canon import APIRouter, Error


class ProjectMissing(Exception):
    pass


project_missing = Error(
    ProjectMissing, status=404, code="project_missing", title="Project missing"
)
router = APIRouter()


def require_project() -> None:
    raise ProjectMissing()


@router.get("/project", raises=[project_missing])
def get_project() -> None:
    require_project()
```

Save this as `src/app.py`. The command above with just `src` passes. Removing
`raises=[project_missing]` produces CAN002. Registering the error only in
`Feature(errors=[project_missing])` supplies a mapping but leaves the operation's
contract missing: CAN001. Both findings include the resolved call path.

A declaration describes an allowed error; it does not catch an exception by
itself. Canon's runtime error mapping converts registered application exceptions
to Problem Details. Source checking checks those contracts separately from
runtime registration and application composition.

## What completeness means

The scope is explicit exceptions defined in the scanned application roots,
including your shared libraries. Include every relevant application library root:
a library you own must not become an external dependency merely because it is
installed separately.

Third-party and built-in exceptions do not need application `Error` mappings.
An unexpected SQL, browser, filesystem or interpreter failure can still produce
500. You do not need to inventory those libraries' internal exceptions. If your
adapter catches such a failure and raises `ProjectMissing`, the checker follows
that translation and checks `ProjectMissing`.

`complete: true` means no unresolved path remains within the supported source
model for that operation. It does not establish that every runtime failure is
handled, that every possible Python protocol was modeled, or that the operation
has no contract gaps. A fully analyzed operation can still have CAN001/CAN002.
See [analysis boundaries](#analysis-boundaries) for unsupported behavior.

The command is the supported entry point. Modules under `checks` and their types
are private implementation details; documented JSON fields are the machine
interface. The expanded value, dependency and resource analysis described below
is **unpublished and planned for 0.20.0**. Re-scan all application roots before
upgrading CI: newly visible application exceptions can fail `--strict`.

## Findings and CI

| Code | Meaning |
| --- | --- |
| `CAN001` | An escaping application exception has a catalog mapping missing from the operation's combined `raises`. |
| `CAN002` | An escaping application exception has no mapping in `raises` or the scanned application catalog. |
| `CAN003` | A declaration, value or execution path cannot be resolved within the supported model. |

Default mode prints findings and exits zero. `--strict` exits one for CAN001 and
CAN002. `--require-complete` independently exits one for CAN003 or a scan with no
supported operations. Use both for a CI gate:

```console
fastapi-canon check errors src libs/orders/src --strict --require-complete --format json
```

Invalid roots, duplicate module names, unreadable files and syntax errors exit
two. Use `fastapi-canon explain CAN003` for diagnostic help, or
`fastapi-canon check errors --help` for flags and defaults.

JSON contains `operations`, `contract_gaps` and `incomplete_operations`. Each
operation includes its `function`, source `path` and `line`, `transport`,
`diagnostics`, `complete` and `scheduled_executions`. Diagnostics include a code,
message, location and call trace. `scheduled_executions` lists resolved coroutine
functions scheduled as tasks; it does not certify their background execution.
An awaited task contributes its application exceptions to the awaiting operation.

Establish a gate over a coherent application slice and all its shared calls.
Check the operation count as well as completeness: an empty scan establishes no
coverage. Remove a known `raises` entry as a negative control, require CAN001 or
CAN002 and exit one, then restore it and require zero gaps. Retain runtime tests
for intended Problem Details responses and transport behavior.

## How the check works

The checker resolves source imports, explicit re-exports, aliases, classes and
calls. It combines policy, router and operation `raises`. Named `Error` values
referenced by `raises=` or `errors=` form the scanned application catalog.
Exception matching follows resolved inheritance order, preferring the most
specific mapping. Contracts do not propagate through separately included routers
or channels.

Supported `try`/`except` removes caught exceptions and includes translated errors
and bare re-raises. Handler state conservatively combines values at possible
failure points. Literal and type guards can exclude branches; arbitrary business
conditions and persisted invariants are not proved. A finding describes a possible
source path, not proof that a particular request reaches it.
Optional inputs retain their present and absent paths without enumerating every
combination of optional parameters.
Assertions contribute their test and possible failure-message calls. They do not
exclude subsequent paths, because Python can disable assertions with `-O`.

Unshadowed module-name guards such as `if __name__ == '__main__'` follow import
semantics. Script-only startup calls do not make imported route contracts
uncertain. Reassigned module names and other runtime conditions remain unresolved.

### Calls and return types

The checker follows actual positional and keyword arguments, defaults and known
`*args`/`**kwargs` values into helpers and constructors. It preserves bound
receivers, constructor fields, aliases, supported container contents and stable
local-function captures. Reassigning an aliased object's fields remains visible
to later calls. Annotation-only statements do not replace an existing value.
Native list/set additions, dictionary updates and `setdefault` preserve stored
values, including mutations shared between operations. Storing a callable does
not execute it. Unknown iterable extensions retain CAN003; deletions conservatively
retain values that another operation may have observed earlier.
Custom application hashing/equality during native mutations remains CAN003.
Dictionary unions retain their contained values and known right-hand key
overrides. An unresolved operand does not establish safe contents.
Raising an exception selected from a supported container checks every possible
exception class. An unresolved alternative retains CAN003 alongside known gaps.
Lambda defaults are evaluated at creation; their bodies are followed when called.
Stable parameter and local captures retain their values. Reassigned cells,
nonlocal mutation and captures changing across loop iterations remain unresolved.

Inherited methods, classmethods, properties, `Self` and statically resolved
`super()` dispatch follow the application's method resolution order. Boolean
operators and conditional expressions follow their evaluation order and narrow
guarded values. Known own `__bool__` and `__len__` methods contribute their errors
when conditions evaluate them. Unexecuted branches add no error paths.
Recursive
call chains are supported when their value and error summaries converge. Growing
or unresolved recursion produces CAN003 rather than a complete result.

`Any`, `object`, unknown callable inputs and unbound `type[Service]` values do not
establish a safe implementation. Unknown locals shadow same-named globals.
Opaque argument unpacking cannot be replaced by parameter annotations. Unresolved
injected types remain incomplete even when the endpoint does not use them:
constructing a dependency can itself raise an application error.
Dynamic decorators, custom allocation and custom attribute lookup remain explicit
boundaries rather than being assumed harmless.

For FastAPI-supplied endpoint and dependency parameters, `HTTPConnection` is
narrowed to `Request` on HTTP/SSE/WebRTC operations. Known framework
`HTTPException` aliases need no application mapping. A possible
`WebSocketException` escaping an HTTP operation retains CAN003.

### Dishka ports

Typed provider factories retain distinct nominal PEP 695 generic keys such as
`Port[First]` and `Port[Second]`, including in transitive constructor dependencies.
Unknown type arguments, missing factories, custom class subscription and generic
class-constructor registrations remain incomplete. Type arguments are not erased
to recover a binding. Unresolved construction is reported even for unused
injected parameters.

Abstract methods and protocol members follow implementations registered by
`Provider` classes in the scanned roots. Supported registrations include
`provide(Impl, provides=Port)`, `@provide`, `alias(source=Impl, provides=Port)`,
constructor-returning factories and transitive providers returning a registered
parameter through local aliases. Quoted class annotations are supported.

The provided value carries its actual constructor arguments. An explicit
`Service(RiskyRepository())` is not replaced by a different registered default.
Multiple bindings contribute their possible errors. Missing registrations,
unsupported component-qualified bindings, ambiguous annotations and dependency
cycles retain CAN003. Providers are read, never executed: instance-level runtime
registrations and `dependency_overrides` are not inferred.

Dependency construction and resource-provider execution **before the yield**
contribute errors to the operation. The yielded implementation is followed through
ordinary `try/finally` resource factories. Post-yield shutdown is a different
lifecycle boundary and does not enter the HTTP operation's `raises` contract.
Conditional resource entries with an early yield and return retain each possible
implementation and its entry effects. Yields inside loops or exception handlers
still require unsupported continuation analysis.
Application startup and whether a provider has already been initialized are not
proved by this source scan.

### Library calls

The checker locates dependencies without importing them. External source
signatures can supply return shapes; external exception-raising bodies are not
traversed. Packages overlapping a scanned application root are not accepted as
external implementations. Missing packages and unresolved method receivers
retain CAN003.

Application callbacks passed to library calls are followed conservatively.
Supported library-generated constructors follow application hooks such as
`__post_init__` and `model_post_init`, along with application field factories.
Opaque callback inputs do not acquire concrete values from their annotations.
Methods invoked indirectly on stored non-callable objects, arbitrary library
protocols and global callback registries remain outside this model.

Bounded return-shape summaries cover standard containers, supported SQLAlchemy
result wrappers and Pydantic models. They describe values, not library error
inventories. Known ORM entities passed to `Session.get`, SQLModel `exec` and
SQLAlchemy `execute`/`scalars` retain their row types through supported select,
result and scalar-result operations. Generic `Self` return types preserve their
type arguments. Free SQL, unknown entity arguments and custom application column
converters remain incomplete; their database internals are not scanned.

Pydantic `Any` remains opaque; it is not treated as JSON. Validated
`JsonValue` and successful JSON-mode dumps have JSON value shapes. Model validation,
after validators, copies and actual field values preserve application objects;
unvalidated `model_copy(update=...)` updates are not assumed validated.
Before field validators for scalar values inspect their unvalidated inputs while
successful native validation supplies the final scalar type. Opaque inputs retain
CAN003 when their calls cannot be resolved. Model-before, container-before, plain
and wrap transformations remain incomplete. Pydantic's wrapped validation
and serializer failures do not become escaping domain exceptions merely because
a hook raised them. Python-mode custom serializer results remain opaque.
Unmodeled `Annotated` validators and application schema hooks retain CAN003.
Constructing a `TypeAdapter` does not run model validation; validating through a
resolved model adapter follows its application hooks and actual input values.
Adapters for native containers of models and model unions follow nested model
hooks and preserve their validated values. `Any` preserves the input; `JsonValue`
supplies recursive JSON shapes. Standard dataclass adapters retain fields,
enum literal types and application `__post_init__` effects. Dataclass inheritance,
custom construction, factories, init-only fields and validator metadata remain
incomplete. Pydantic-wrapped validation failures do not escape as domain errors.
Functional `Annotated` metadata and custom schema
implementations remain incomplete unless explicitly supported. A single scalar
`BeforeValidator` function follows its raw input; `AfterValidator` preserves its
replacement value. Nested and chained before transformations remain CAN003.
`BaseSettings` construction with standard sources retains native environment
input shapes and possible overrides of defaults. Explicit model fields override
only those fields. Custom sources, custom initialization, opaque unpacking and
source-control arguments such as `_env_file` remain CAN003.
Validated `SecretStr` and `SecretBytes` retain their native payload shapes.
Direct wrappers, existing instances, subclass dispatch and later payload writes
retain actual values; an unresolved payload remains CAN003.
External `contextmanager` and `asynccontextmanager` signatures preserve their
yielded types, including type parameters bound to actual arguments. Their bodies
are outside the error scope. Own objects, unknown arguments and application
callbacks that could affect the external protocol retain CAN003. Native HTTP
clients with default transports preserve their value through provider entry;
custom authentication, event hooks, mounts and transports remain boundaries.

## Comprehensions and deferred generators

Eager list, set and dict comprehensions follow their iterables, filters and
results with local target bindings. Native `reversed` views retain element types
and later container mutations; custom reverse protocols remain incomplete.
Directly resolved generator iteration and
supported eager consumers follow generator bodies. Unsupported iteration
protocols and unresolved element methods retain CAN003. Resolved generator-function
aliases preserve bound arguments until iteration, including with `closing` and
`aclosing`. Closing an unstarted generator does not execute its body. Unknown
unpacked arguments and stored generator-expression aliases remain incomplete.

Creating a generator expression runs its outer iterable expression, not its
result helpers. Unconsumed generators and unawaited coroutines retain deferred
CAN003. `asyncio.create_task` records a separate scheduled execution; awaiting a
resolved task follows its body. Supported `gather` handling preserves exceptions
from evaluating arguments even with `return_exceptions=True`. Background work is
not certified by a complete HTTP operation.

## Local context managers

Supported local sync/async context methods and single-yield
`contextmanager`/`asynccontextmanager` factories contribute entry, body and cleanup
errors. Managers enter left to right and exit in reverse order. Known suppression
removes caught body errors; uncertain suppression retains possible errors and
CAN003. Exception-group splitting is not modeled.

Known standard-library resource shapes include `suppress`, `closing`,
`aclosing`, `asyncio.Lock` and `asyncio.timeout`. Owned `ExitStack` and
`AsyncExitStack` registrations retain callback/context effects until explicit
close or scope exit, including when the stack is stored in application fields.
Conditional registration does not establish definite suppression. Opaque stacks,
unsupported `push` registrations and ownership transfer remain CAN003. A type
annotation alone does not establish a stack's registered effects.

## Transports

| Declaration | Compared with |
| --- | --- |
| `@router.get(...)` and other HTTP methods | Endpoint and dependency errors against policy, router and operation `raises`. |
| `@router.sse(...)` | Dependency and iterated stream errors against router and stream `raises`. |
| `@router.webrtc(...)` | Session factory and dependency errors against router and session `raises`. |
| `@channel.method` on `RpcChannel` | Method errors against channel and method `raises`. |
| `@channel.stream(...)` on `RpcChannel` | Iterated stream errors against inherited channel and stream `raises`. |
| `router.channel(...)`, `router.socket(...)`, `router.binary_stream(...)` | Recognized declarations; unresolved connection, rejection and close contracts produce CAN003. |

For SSE, the same declaration covers dependency failures before response start
and terminal `canon.problem` events during streaming. Source completeness does
not validate runtime handling after a response starts.

### RPC channel subclasses

Since Canon 0.19.0, simple statically resolved `RpcChannel` subclasses are
recognized. Supported chains have one base per class and only `pass` or docstrings
in class bodies, without decorators, metaclass keywords or type parameters.
Imports, re-exports and aliases are followed. Recognized subclasses with custom
class logic remain in the report with CAN003.

The checker reads `Error` and channel/method `raises`. `RpcError` subclasses and
`RpcErrorBinding` contracts remain unresolved. Unrelated `.method` decorators are
ignored. Included-channel contracts do not propagate.

**Unpublished, planned for 0.20.0:** child channels created with `channel.child(...)`
are recognized, including imported aliases, and inherit their parent's `raises`.
Direct registrations such as `router.socket('/raw')(handler)` and saved decorator
aliases remain visible, including inside imperative factories. Factory composition
and socket-specific contracts retain CAN003 until they can be resolved. Creating
a decorator without applying it does not register an operation. JSON uses
`transport: "socket"` for standalone sockets and binary streams; RPC methods and
streams use `transport: "rpc"`.

## Direct local route factories

Supported local router/channel declarations and stable helper captures are
indexed without calling their enclosing factory. Straightforward local contracts,
aliases and collections are resolved. A declaration independent of an enclosing
application factory's dynamic setup can also be inspected without executing that
setup.

Dynamic captures, reassigned declarations, conditional router selection and
unsupported factory execution retain CAN003. The scan reports source declarations,
including uncalled factories. It does not establish mounting, URL prefixes,
reachability or successful application construction.

## Performance in CI

Pass relevant roots in one invocation. Parsed modules and summaries are reused
within that invocation, including dependencies on changing object values.
Caches are discarded afterward. Measure on your CI runner before setting a time
budget. Repository maintainers can use:

```console
uv run --locked python scripts/benchmark_error_contracts.py --output dist/checker-benchmark.json
```

The benchmark records source processing and fresh CLI invocation separately.
`--profile dist/checker.prof` captures a profile; `--compare` compares report
fingerprints with an earlier benchmark.

## Analysis boundaries

This is a bounded static analysis of explicit application error contracts.
Implicit interpreter failures, arbitrary operator/descriptor/iteration protocols,
third-party exception internals, startup, resource shutdown and independent
background executions are outside the operation contract. They can still produce
500 or fail at their own lifecycle boundary.

Unknown call targets, unbound ports, dynamic contracts, unsupported decorators,
unstable captures, unsupported context ownership and nonconverging value/effect
summaries produce CAN003. Analysis limits also produce CAN003; they do not silently
turn an operation complete. `match` and `except*` remain unsupported. Loops combine
possible paths rather than proving iteration counts. Finally blocks that may
replace an escaping exception remain incomplete.

Plain FastAPI routers are skipped. The checker does not validate composed
registries, mounting or runtime overrides. Including routers into a Canon parent
with additional contracts can therefore leave affected operations incomplete and
suppress guessed gaps. Retain composition validation and runtime tests alongside
the source gate. A passing result establishes only the documented source scope.
