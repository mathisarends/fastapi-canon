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
from fastapi_canon import CanonRouter, Composition, Feature

projects = CanonRouter(prefix="/projects")


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
    composition.apply(app)
```

`check` does not install routes, handlers, middleware or a container on the target
application. It does not enter lifespans, resolve services, start subscribers,
connect to a database or apply migrations. It can execute provider and router
factories to inspect their configuration. Keep these factories free of resource
acquisition and other startup work.

`apply` uses the same preparation and checks, and raises
`FeatureConfigurationError` for errors before installation. A separate `check`
invocation is optional. Calling `check` followed by `apply` performs two separate
preparations, so factories execute once in each call. A check of an already
installed composition reuses its installation snapshot and does not rematerialize
providers. `validate(app)` still requires the matching installed composition;
it validates OpenAPI and raises for system-check errors, including custom checks.
Missing optional runtimes appear as findings in `check`; `apply` retains its
existing `ModuleNotFoundError` contract for those missing dependencies.

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
| `skipped` | A prerequisite failed, so this check could not run. |
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
| `canon.mcp.E002` | An MCP auth or middleware factory cannot be constructed. |
| `canon.handlers.E001` | Exception-handler conflicts. |
| `canon.errors.E001` | Incompatible feature/policy error catalogs. |
| `canon.providers.E001` | Provider sources, instrumentation declarations or the Dishka graph are invalid. |
| `canon.description.E001` | HTTP metadata cannot be described against the composed catalog. |
| `canon.contracts.E001` | HTTP/OpenAPI response contracts cannot be compiled. |
| `canon.checks.E001` | A feature check failed to return valid findings. |

Built-in checks validate composition configuration, the provider graph and HTTP
contracts. They do not infer whether a dependency enforces authentication, require
authentication on every route, prove exception coverage, or validate the runtime
behavior of RPC, SSE event bodies, WebRTC, MCP or application services. An SSE
route's HTTP admission surface is included when represented by an HTTP route.
Database/deployment probes remain explicit operations outside this profile.

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
Canon routers retain the declared symbol across framework wrappers such as
`DishkaRoute`, so automatic injection does not change the lookup reference.

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
fastapi-canon inspect app.main:create_app --factory
```

Composition checking imports the target and checks against a fresh FastAPI app by
default. `--app module:app` selects a configured application, including an already
installed one. Introspection imports an installed app; `--factory` explicitly
calls an application factory. Neither command enters its lifespan. Imports and
user factories execute application code; this is not an import-free mode.

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
