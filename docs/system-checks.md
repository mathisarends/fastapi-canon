# Composition checks and introspection

Use `Composition.check(app)` before installation to inspect configuration problems,
and `Composition.inspect(app)` after installation to explain the effective HTTP
composition. Both use immutable values exported from `fastapi_canon`.
For HTTP/SSE, RPC and WebRTC schema documents, use the complementary
[application contract export](contracts.md); introspection describes ownership
and declaration provenance.

## Check before installation

```python
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature

projects = APIRouter(prefix="/projects")


@projects.get("/{project_id}", operation_id="projects.get")
async def get_project(project_id: int) -> dict[str, int]:
    return {"id": project_id}


composition = Composition(features=(Feature(name="projects", routers=[projects]),))
app = FastAPI()
report = composition.check(app)

for issue in report.issues:
    print(issue.code, issue.message, issue.feature_names, issue.locations)
    print(issue.hint)

if not report.has_errors:
    composition.build(app)
```

`check` does not install routes, handlers, middleware or a container on the target
application. It does not enter lifespans, resolve services, start subscribers,
connect to a database or apply migrations. It can execute provider and router
factories to inspect their configuration. Keep these factories free of resource
acquisition and other startup work.

`build` uses the same preparation and checks, and raises
`FeatureConfigurationError` for errors before installation. After installing, it
checks the installed application again, including custom checks and OpenAPI, and
returns it. A separate `check` invocation is optional. Calling `check` followed by
`build` performs two separate preparations, so factories execute once in each
call. A check of an already installed composition reuses its installation snapshot
and does not rematerialize providers. Building the same composition again only
repeats these checks; building a different one into the same application fails.
Missing optional runtimes appear as findings in `check`; `build` retains the
`ModuleNotFoundError` contract for those missing dependencies.

Invalid constructor arguments still fail at construction time. A report is
available for a successfully constructed `Composition`, including configurations
whose contributions conflict during installation.

## Findings and coverage

Each `CheckIssue` has a stable `code`, `severity` (`error` or `warning`), `message`,
`hint`, `feature_names` and `locations`. Provider diagnostics name participating
features and retain Dishka's missing-binding path in the message. `CheckResult`
records each check's `name` and `status`:

| Status | Meaning |
| --- | --- |
| `passed` | Finished without errors; warnings may still be present. |
| `failed` | Finished and found an error. |
| `skipped` | A prerequisite failed or dependency resolution belongs to an external/unsupported profile. |
| `incomplete` | A feature check could not produce a valid result. |

`report.has_errors` and `report.complete` answer separate questions. A fully
executed failing check is complete. Skipped or incomplete checks make `complete`
false. Completeness covers the named checks in that report, not all possible
application behavior. Independent failures can appear together.

| Code | Configuration problem |
| --- | --- |
| `canon.composition.E001` | Installation timing, conflicting installation or integration options. |
| `canon.routing.E001` | Repeated router contributions or an invalid router factory. |
| `canon.routing.E002` | Duplicate HTTP operation IDs, including collisions with existing app routes. |
| `canon.routing.E003` | Ambiguous HTTP method/path contributions, including hidden and existing app routes. |
| `canon.routing.E004` | Duplicate effective WebSocket paths across features or existing app routes. |
| `canon.mcp.E001` | An application or feature HTTP route shadows the MCP endpoint. |
| `canon.mcp.E002` | An MCP auth or middleware factory cannot be constructed. |
| `canon.handlers.E001` | Exception-handler conflicts. |
| `canon.errors.E001` | Incompatible feature/policy error catalogs. |
| `canon.providers.E001` | Provider sources, instrumentation declarations or the Dishka graph are invalid. |
| `canon.providers.E002` | A mounted JSON-RPC method has a missing service root or unreadable dependency declarations. |
| `canon.providers.E003` | An HTTP/SSE endpoint root is unavailable at its execution scope, or its declarations are unreadable. |
| `canon.providers.E004` | A WebRTC factory injects a service unavailable through APP/SESSION scope. |
| `canon.description.E001` | HTTP metadata cannot be described against the composed catalog. |
| `canon.contracts.E001` | HTTP/OpenAPI response contracts cannot be compiled. |
| `canon.checks.E001` | A feature check failed to return valid findings. |

Built-in checks validate composition configuration, the provider graph, selected
HTTP/SSE, RPC and WebRTC factory roots, HTTP contracts and registered WebSocket path
uniqueness during preflight. They do not
infer whether a dependency enforces authentication, require
authentication on every route, prove exception coverage, or validate the runtime
behavior of RPC, SSE event bodies, WebRTC, MCP or application services. An SSE
route's HTTP admission surface is included when represented by an HTTP route.
Database/deployment probes remain explicit operations outside this profile.

WebSocket path preflight includes native routes and JSON-RPC channels across
features, nested routers and existing application routes. A shared
`router_factory` prefix is included, and its factory is called only once per
preparation. `canon.routing.E004` identifies both owners and endpoint names
before routes or a container are installed. Parameter names do not distinguish
routes; converter equivalence means identical compiled regex structure after
named capture labels are removed. Default and explicit `str` converters are
equivalent on native routes. This does not extend JSON-RPC's supported path syntax
or attempt arbitrary overlap analysis. HTTP and WebSocket URLs are separate ASGI
scope types. Mounted subapplications remain separate ownership boundaries.
Checks of an already installed app retain the HTTP-oriented snapshot profile;
declare WebSocket contributions before building a new app to run this preflight.

The `http.paths` check compares effective HTTP paths after nested and composition
prefixes. Parameter names do not distinguish routes: `GET /items/{item_id}` and
`GET /items/{other_id:str}` conflict. Converter equivalence means identical
compiled regex structure after named capture labels are removed, including
default versus explicit `str` converters. Different converter regexes remain
distinct even when they overlap (such as `int` and `float`); the check does not
attempt to prove arbitrary regex equivalence or overlap. Static and parameter
routes can intentionally overlap, and distinct methods or prefixes are valid.
WebSocket routes use another ASGI scope and do not collide with HTTP routes.
Mounted subapplications retain their separate ownership boundary. Resolve an
`E003` finding by changing a path/method or removing the duplicate registration;
distinct operation IDs and schema visibility do not change request dispatch.

## HTTP and SSE endpoint dependency preflight

Preflight reads endpoint `Inject[T]` and `FromDishka[T]` roots for Canon's default
`InjectedRoute`, native `DishkaRoute` and recognized Dishka-decorated HTTP routes.
`canon.providers.E003` identifies the feature, effective method/path, service and
component when the prepared graph cannot supply a root at its execution scope.
Both a missing container and an unrelated valid provider graph are diagnosed.
Missing service bindings reject `build()` before installing routes or scopes;
unreadable annotations produce an incomplete error without exposing exception
details. Checking does not resolve providers, enter lifespans or open resources.

HTTP async/sync handlers can use bindings through REQUEST scope, including APP
and SESSION parents. Typed Canon SSE bodies run after HTTP REQUEST cleanup:
their injected services must be available at APP scope. This applies to both
async and sync generators; resolving an argument earlier does not extend its
resource lifetime. Use an APP service, or inject an APP-scoped `ServiceScope`
factory and open short independent REQUEST scopes inside stream work. Canon
does not retain the closed HTTP scope until disconnect. Missing or REQUEST-only
stream bindings now fail preflight instead of becoming terminal problems or
closed-resource access during iteration.

`http.endpoint_dependencies:<feature>:<methods>:<path>` records each supported
check. Arbitrary custom route classes and externally installed containers retain
their ownership and are `skipped`, without resolving their services or replacing
their container. Such reports are incomplete for `--require-complete`.
FastAPI `Depends`/`Security` functions are outside this profile, including sync
functions already transformed by Dishka into `Depends` declarations. Existing
async `@inject` wrappers retain inspectable original annotations. Successful
checks do not prove runtime context availability or activation conditions.
Ordinary request parameters and schemas are unchanged; injected roots stay out
of OpenAPI. This is preflight coverage; installed snapshots keep their existing
HTTP contract/path checks.

## WebRTC factory dependency preflight

Automatically injected WebRTC factory roots are checked through APP/SESSION
scope. `canon.providers.E004` reports the feature, effective WebSocket path,
service and component. REQUEST resources belong in explicit short child scopes
for message work; a factory cannot resolve them from its SESSION container.
The check records `webrtc.factory_dependencies:<feature>:<path>` and never runs
providers or the factory. External containers and uninspectable explicit Dishka
wrappers are skipped. Arbitrary nested FastAPI `Depends`/`Security` dependencies,
including markers already replaced by a synchronous `@inject` decorator, are
outside this root check. Runtime admission, callback behavior and media engines
remain outside composition preflight. See the [WebRTC scope guide](integrations/webrtc/canon-dishka.md).

## RPC method dependency preflight

For mounted JSON-RPC methods using Canon's automatic Dishka resolver, preflight
checks each application-service `Inject[T]` root against the prepared container's
default component, through APP, SESSION and REQUEST scope. A provider in ACTION
or STEP is not available to an ordinary RPC call. Missing bindings produce
`canon.providers.E002` with the feature, effective WebSocket path, method and
qualified service type, including when no container or only unrelated providers
exist. Register the service in `Feature(providers=[...])`; the provider graph
checks its own dependency edges. No service is resolved and no scope or lifespan
is entered. Checking snapshots channels without freezing caller-owned declarations.

`RpcConnection`, typed path models, the declared connection context and hook
results actually reachable from that context are supplied by the adapter.
An unused registered hook does not supply its result to method injection.
Context/hook **service inputs**, FastAPI admission dependencies and runtime
activation conditions are outside this method-root check.

Each `rpc.method_dependencies:<feature>:<path>:<method>` result describes this
bounded check. Explicit resolvers (including explicit Dishka adapters), custom
resolver scopes/handlers, dynamic `before_accept` values and externally installed
containers retain their ownership; application roots that cannot be verified
without executing them are `skipped`. Injected notification/subscription roots
and binary/raw endpoint profiles are also skipped. They add no missing-binding
error, so `build()` remains supported, but make a preflight report incomplete.
An unreadable declaration is an incomplete error without exposing exception
details. `--require-complete` rejects an incomplete report; it does not execute
external resolvers to establish coverage. Installed-app checks retain their
HTTP snapshot profile. This is not a claim of complete transport/runtime validation.

## Explain the installed HTTP composition

```python
description = composition.inspect(app)
operation = description.operation(get_project)

assert operation.feature_name == "projects"
assert operation.path == "/projects/{project_id}"
assert operation.methods == ("GET",)

for dependency in operation.dependencies:
    print(dependency.name, dependency.origin)

for problem in operation.errors:
    print(problem.code, problem.type_uri, problem.status, problem.title, problem.origin)
```

The snapshot contains ordered `features` and `operations`. Each `FeatureInfo`
contains its operations and qualified provider-source names. Each `OperationInfo`
contains its feature, effective path, HTTP methods, operation ID, schema visibility,
dependencies and declared HTTP errors. Prefixes from nested routers and
`router_factory` are included. Hidden HTTP routes remain visible with
`include_in_schema=False`. Use `operation(endpoint)` for Python symbol references
and `operation_by_id("projects.get")` for external IDs. Both reject missing or
ambiguous matches. Callable lookup uses identity and never invokes the endpoint;
decorated routes use the callable registered with the router. Runtime lookup
references are excluded from dataclass/JSON exports.
Canon routers retain the declared HTTP/SSE symbol across framework wrappers such as
`DishkaRoute` or Canon's own injection, so injection does not change the
lookup reference.
For SSE, use the generator callable passed to the stream declaration, including
when automatic injection and terminal processing wrap it. Explicit user decorators
keep the registered callable's identity; lookup does not unwrap those decorators.

Origins distinguish `application`, `composition.router_policy`,
`composition.router_factory`, `router.include_router`, `router.policy`, `router`,
`operation` and nested/parameter `dependency` declarations where applicable.
Plain FastAPI response metadata without a Canon declaration uses `route` for
its error origin. These describe provenance, not an authentication property.

Canon records provenance automatically without adding implementation fields to
exported OpenAPI. Exported values contain names and public contract metadata, not routers,
provider objects, service instances, credentials or configuration values.
The description is a detached snapshot recorded during installation. Later router
mutations do not rewrite it; build a new application for a new composition.
Routes outside the composition participate in collision checks but are not listed
as feature contributions in the snapshot. Checks label their owner `<application>`.
Mounted subapplications are separate ownership boundaries and are not traversed
by this HTTP composition profile. Custom OpenAPI rewrites are not reflected in
the installation snapshot.

## Add a feature check

```python
from fastapi_canon import CheckContext, CheckIssue, Feature


def check_export(context: CheckContext) -> list[CheckIssue]:
    if any(op.operation_id == "reports.export" for op in context.feature.operations):
        return []
    return [
        CheckIssue(
            code="reports.W001",
            severity="warning",
            message="No report export operation is configured.",
            hint="Declare reports.export if report downloads are required.",
        )
    ]


reports = Feature(name="reports", checks=[check_export])
```

Checks are synchronous, finite functions receiving a read-only `CheckContext`
with `composition` and the current `feature`. Return a list or tuple of
`CheckIssue` values. Canon fills omitted `feature_names` with the declaring
feature's name. Use an application-specific code namespace. Checks run during
preparation, explicit checks and validation; keep them deterministic and free of
I/O and side effects. Uncaught exceptions or invalid results produce an incomplete
error finding without exposing exception text. They prevent installation.

## CLI and JSON

Export a composition from a bootstrap module without installing or starting the
application on import:

```console
fastapi-canon check composition app.bootstrap:composition
fastapi-canon check composition app.bootstrap:composition --format json --require-complete
fastapi-canon check composition app.bootstrap:composition --app app.main:app
fastapi-canon inspect app.main:app --format json
fastapi-canon inspect app.main:create_app
```

Composition checking imports the target and checks against a fresh FastAPI app by
default. `--app module:app` selects a configured application, including an already
installed one. Both application targets also accept synchronous zero-argument
factories automatically; `inspect --factory` remains a compatibility flag.
The composition target itself remains a `Composition` instance. Neither command
enters lifespan. See the shared [target loading rules](contracts.md#application-target-rules).
Imports and
user factories execute application code; this is not an import-free mode.

Use `fastapi-canon inspect --help` or
`fastapi-canon check composition --help` for target syntax and policy defaults.
Composition defaults to `--fail-on error`; choose `warning` to include warning
findings in exit 1. `--require-complete` independently rejects skipped or
incomplete checks with exit 2. It does not run unmodeled runtime behavior or
establish complete exception coverage. The source check's `--require-complete`
policy uses exit 1; see [source checking](error-checking.md).

JSON output has `schema_version: 1` and the corresponding dataclass fields.
Check reports also include `has_errors` and `complete`. Exit codes are 0 for
success, 1 for findings at the selected threshold, and 2 for invalid targets or
incomplete reports when `--require-complete` is selected. Errors fail by default;
use `--fail-on warning` to fail for warnings too. Text and JSON use the same API.

The existing source-only checker remains separate and never imports application
code:

```console
fastapi-canon check errors src --strict --require-complete
```

See [source error checking](error-checking.md) for its coverage and exit policy.
