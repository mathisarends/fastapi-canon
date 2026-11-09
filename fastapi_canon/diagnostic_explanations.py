"""Offline CLI explanations; emitted codes remain owned by their checkers."""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class _Explanation:
    meaning: str
    example: str
    repair: str
    guide: str


_DOCS = "https://github.com/mathisarends/fastapi-canon/blob/main/docs/"
_CHECKS = _DOCS + "system-checks.md#findings-and-coverage"
_CODEGEN = _DOCS + "unified-canon-concept.md#json-generation-diagnostics"
_EXPLANATIONS = {
    "CAN001": _Explanation(
        "A reachable application exception has a known Error mapping but is missing from the operation's raises declaration.",
        "An existing missing = Error(Missing, ...) is registered; read() raises Missing, but @router.get('/items') omits raises=[missing].",
        "Reference the existing Error in raises=[missing] on each affected operation; keep one mapping for the exception.",
        _DOCS + "error-checking.md",
    ),
    "CAN002": _Explanation(
        "A supported explicit application exception path has no statically known Error mapping.",
        "@router.get('/items')\ndef read():\n    raise Missing()  # No Error(Missing, ...) is declared.",
        "Declare Error(Missing, status=404, code='missing', title='Missing') and include that instance in the operation's raises list.",
        _DOCS + "error-checking.md",
    ),
    "CAN003": _Explanation(
        "A declaration or execution path could not be fully analyzed. This is incomplete source coverage, not a missing-mapping conclusion.",
        "An operation calls a dynamically selected callback whose implementation the scanned source cannot resolve.",
        "Include the relevant import roots and explicit implementations/bindings, or replace unsupported dynamic flow. Review the reported trace and analysis boundary; do not treat incomplete coverage as a clean result.",
        _DOCS + "error-checking.md#analysis-boundaries",
    ),
    "canon.composition.E001": _Explanation(
        "The application cannot accept this composition at its current installation phase or with its current integration options.",
        "A different composition is installed on an app that already owns another installed composition.",
        "Install one compatible composition on a fresh app before startup and schema generation; inspect the finding's integration hint.",
        _CHECKS,
    ),
    "canon.routing.E001": _Explanation(
        "Router contributions are repeated or the application router factory is invalid.",
        "Feature(name='first', routers=[router]) and Feature(name='second', routers=[router]) reuse the same router instance.",
        "Contribute each router once and make router_factory return a fresh empty FastAPI APIRouter.",
        _CHECKS,
    ),
    "canon.routing.E002": _Explanation(
        "Visible HTTP operations have duplicate operation IDs, including collisions with existing app routes.",
        "GET /first and GET /second both declare operation_id='items.read'.",
        "Choose distinct explicit operation IDs or namespaces. Keep their generated consumer names stable and regenerate affected SDKs.",
        _CHECKS,
    ),
    "canon.routing.E003": _Explanation(
        "Two HTTP contributions have an ambiguous effective method/path after prefixes and equivalent parameter patterns are considered.",
        "GET /items/{item_id} and GET /items/{other_id:str} compete for the same route shape.",
        "Change a method/path or remove the duplicate contribution. Different operation IDs or hidden schemas do not change dispatch.",
        _CHECKS,
    ),
    "canon.routing.E004": _Explanation(
        "WebSocket contributions share an effective path, including native routes and RPC channels.",
        "Two features include WebSocket endpoints at /projects/{project_id}/rpc after router prefixes are applied.",
        "Assign distinct effective WebSocket paths or remove duplicate registration. HTTP and WebSocket routes have separate ASGI scopes.",
        _CHECKS,
    ),
    "canon.mcp.E001": _Explanation(
        "An application or feature HTTP route shadows the configured MCP endpoint.",
        "An HTTP /mcp route overlaps the endpoint configured by McpOptions(path='/mcp').",
        "Move the conflicting HTTP route or choose a distinct MCP path, then rerun composition preflight.",
        _CHECKS,
    ),
    "canon.mcp.E002": _Explanation(
        "An MCP authentication or middleware factory cannot be constructed during preflight.",
        "A synchronous auth_factory raises while constructing the configured MCP authentication adapter.",
        "Use a synchronous factory that borrows the APP container without starting resources, and verify its returned adapter and optional MCP dependencies.",
        _CHECKS,
    ),
    "canon.handlers.E001": _Explanation(
        "Features contribute conflicting exception handlers for the same exception type.",
        "Two features each register exception_handlers={Missing: handler}, using different handlers.",
        "Choose one handler owner or share the same handler instance for that exception; inspect all named feature contributions.",
        _CHECKS,
    ),
    "canon.errors.E001": _Explanation(
        "Feature or policy error catalogs contain incompatible definitions.",
        "Two features map Missing to the same public code with conflicting status values, 404 and 409.",
        "Share one compatible Error definition and type-base policy across contributions; preserve the intended public problem contract.",
        _CHECKS,
    ),
    "canon.providers.E001": _Explanation(
        "Provider sources, instrumentation declarations or the prepared Dishka dependency graph are invalid.",
        "A registered Service constructor needs a Port that no provider binds, or a provider factory returns a non-Provider value.",
        "Inspect the named feature/provider and missing-binding path; supply valid Provider sources and scope-compatible graph bindings. Preflight does not resolve runtime services.",
        _CHECKS,
    ),
    "canon.providers.E002": _Explanation(
        "A mounted RPC method has an unavailable injected root or unreadable declarations in the supported automatic-resolver profile.",
        "A channel method accepts store: Inject[Store], but the prepared graph cannot supply Store through REQUEST scope.",
        "Register the default-component service at APP/SESSION/REQUEST scope or use an explicit application-owned resolver. Review skipped coverage for unsupported resolver/context cases.",
        _DOCS + "system-checks.md#rpc-method-dependency-preflight",
    ),
    "canon.providers.E003": _Explanation(
        "An HTTP/SSE injected endpoint root is unavailable at its execution scope, or its declarations cannot be inspected.",
        "An HTTP handler injects an unbound Store, or a typed SSE generator injects a REQUEST-only resource after HTTP cleanup.",
        "Bind HTTP services through REQUEST scope. SSE bodies need APP services or APP-scoped ServiceScope factories for short child-scope work; inspect service/component locations.",
        _DOCS + "system-checks.md#http-and-sse-endpoint-dependency-preflight",
    ),
    "canon.providers.E004": _Explanation(
        "A WebRTC factory injects a service unavailable through its WebSocket APP/SESSION lifetime.",
        "@router.webrtc('/media') uses Inject[Store], while Store is bound only at REQUEST scope.",
        "Register factory services at APP or SESSION scope and use explicit child REQUEST scopes for per-message work. Review external-container and nested-dependency limits.",
        _DOCS + "system-checks.md#webrtc-factory-dependency-preflight",
    ),
    "canon.description.E001": _Explanation(
        "HTTP metadata cannot be described against the composed read-only error catalog.",
        "A declared HTTP error cannot be resolved from the catalog supplied to the metadata adapter.",
        "Check declared error instances and the catalog used by the effective router/policy. Prefer supported raises declarations so composition can collect the same definitions.",
        _CHECKS,
    ),
    "canon.contracts.E001": _Explanation(
        "HTTP/OpenAPI response contracts cannot be compiled consistently with the composed error policy.",
        "An HTTP error response declares a JSON body that conflicts with its required application/problem+json contract.",
        "Align response status, media type and schema with the declared Error and composed catalog; use the finding's HTTP contract hint.",
        _CHECKS,
    ),
    "canon.checks.E001": _Explanation(
        "A synchronous feature check raised an exception or returned invalid findings, leaving its result incomplete.",
        "def check(context):\n    return None  # A list/tuple of CheckIssue values is required.",
        "Return a finite list or tuple of CheckIssue values from a deterministic synchronous check. Keep resource probes and runtime work outside this profile.",
        _DOCS + "system-checks.md#add-a-feature-check",
    ),
    "canon.codegen.I001": _Explanation(
        "Common SDK generation successfully updated generated artifacts.",
        "generate --format json writes a changed client.py and reports its path in changes.",
        "Review and commit the generator-owned contracts/SDK files, then run generate --check for exact drift.",
        _CODEGEN,
    ),
    "canon.codegen.W001": _Explanation(
        "A read-only common generation check found contract or SDK artifact drift.",
        "generate --check --format json finds that a committed client differs from current declarations and exits 1.",
        "Run generation without --check/--diff, review the changes and commit the owned artifacts; avoid hand-editing generated output.",
        _CODEGEN,
    ),
    "canon.codegen.W002": _Explanation(
        "A specialized SSE binding reports a validation constraint that its generated target cannot enforce.",
        "An SSE schema uses format='uri-reference' while its selected SDK target lacks that validator.",
        "Review the source operation, schema pointer and target capability; retain necessary validation at the application boundary. This warning alone does not change the generation exit code.",
        _CODEGEN,
    ),
    "canon.codegen.E001": _Explanation(
        "Common generation failed configuration, dependency, target-loading, contract/planning or artifact-ownership validation.",
        "A source reference cannot import its app, a generation dependency is missing, or an output contains an unowned file.",
        "Follow the source/target location and corrective hint. Check module:attribute and factory selection, install fastapi-canon[codegen] when needed, and use a valid owned output directory.",
        _CODEGEN,
    ),
    "canon.codegen.E002": _Explanation(
        "An operation requests a binding feature unsupported by common generation.",
        "POST /upload contains a nested multipart object, while the common HTTP binding supports flat scalar and file parts.",
        "Use flat multipart fields or a supported JSON/binary contract; review the reported source, operation and schema location for the unsupported encoding.",
        _CODEGEN,
    ),
}


def _main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fastapi-canon explain",
        description="Explain built-in source, composition and common generation diagnostic codes without importing application code.",
        epilog="Examples: CAN003, canon.routing.E002, canon.codegen.E002. Codes are case-sensitive. Exit codes: 0 explained; 2 unknown code or invalid arguments. Application-defined codes use their own CheckIssue hint.",
    )
    parser.add_argument("code", help="Diagnostic code exactly as printed in its report")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Explanation format: text (default) or a versioned JSON object",
    )
    args = parser.parse_args(arguments)
    explanation = _EXPLANATIONS.get(args.code)
    if explanation is None:
        message = (
            f"Unknown built-in diagnostic code {args.code!r}; use fastapi-canon explain --help for supported families and examples. "
            "Application-defined codes use the hint supplied by their check."
        )
        if args.format == "json":
            print(
                json.dumps({"schema_version": 1, "code": args.code, "error": message})
            )
        else:
            print(message, file=sys.stderr)
        return 2
    if args.format == "json":
        print(
            json.dumps(
                {"schema_version": 1, "code": args.code, **asdict(explanation)},
                indent=2,
            )
        )
    else:
        print(f"{args.code}\n\n{explanation.meaning}")
        print(f"\nExample:\n{explanation.example}")
        print(f"\nRepair:\n{explanation.repair}")
        print(f"\nGuide: {explanation.guide}")
    return 0
