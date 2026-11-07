from __future__ import annotations

import ast
import json
import posixpath
import re
import subprocess
import sys
from dataclasses import fields, is_dataclass, replace
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import indent
from typing import Any
from urllib.parse import urlsplit

from fastapi_canon.codegen.config import TargetConfig
from fastapi_canon.codegen.ir import (
    GenerationError,
    Operation,
    Parameter,
    Plan,
    _GenerationWarning,
)
from fastapi_canon.codegen.names import camel, model_field, pascal, snake
from fastapi_canon.codegen.problems import problem_runtime


def render(
    plan: Plan,
    target: TargetConfig,
    *,
    diagnostics: list[_GenerationWarning] | None = None,
) -> dict[str, str]:
    from fastapi_canon.integrations.sse.codegen.enums import with_enums
    from fastapi_canon.integrations.sse.codegen.ir import (
        Contract,
        Declaration,
        StringEnum,
        _Types,
    )
    from fastapi_canon.integrations.sse.codegen.python import _Models
    from fastapi_canon.integrations.sse.codegen.python import (
        runtime_source as sse_runtime,
    )
    from fastapi_canon.integrations.sse.codegen.typescript import generate_typescript

    if target.language == "python" and any(
        source.kind == "webrtc" for source in plan.sources
    ):
        raise GenerationError(
            "WebRTC sessions require a TypeScript target; no Python media engine is generated"
        )
    if target.client_name in (
        {model.name for model in plan.models}
        | {source.name + "Client" for source in plan.sources}
        | {"Bindings", "Clients"}
        | (
            {"Upload"}
            if target.language == "python"
            and any(
                param.location == "file"
                for operation in plan.operations
                for param in operation.parameters
            )
            else set()
        )
    ):
        raise GenerationError("client-name collides with a model or binding name")
    schemas = {model.name: model.schema for model in plan.models}
    types = _Types({"components": {"schemas": schemas}})
    declarations = []
    for name in schemas:
        types.parse({"$ref": f"#/components/schemas/{name}"})
    for operation in plan.operations:
        for parameter in operation.parameters:
            declarations.append(
                Declaration(
                    _alias(operation, parameter.name), types.parse(parameter.schema)
                )
            )
        for direction, schema in operation.binding.metadata.get("messages", {}).items():
            declarations.append(
                Declaration(_alias(operation, direction), types.parse(schema))
            )
        if operation.interaction != "session":
            result_schema = operation.result
            if target.language == "typescript" and operation.binding.protocol == "http":
                metadata = operation.binding.metadata
                results = []
                for status, schema in metadata["responses"].items():
                    if status not in metadata["bodyless"] and schema not in results:
                        results.append(schema)
                if results:
                    result_schema = (
                        results[0] if len(results) == 1 else {"anyOf": results}
                    )
            declarations.append(
                Declaration(_alias(operation, "Result"), types.parse(result_schema))
            )
            if operation.binding.protocol == "http" and (
                target.language == "python" or operation.binding.metadata.get("binary")
            ):
                metadata = operation.binding.metadata
                declarations.extend(
                    Declaration(
                        _alias(operation, "Response" + status), types.parse(schema)
                    )
                    for status, schema in metadata["responses"].items()
                    if status not in metadata["bodyless"]
                )
    declarations = [*types.declarations(), *declarations]
    if len({declaration.name for declaration in declarations}) != len(declarations):
        raise GenerationError(
            "An operation type name collides with a model; rename the model or operation"
        )
    contract = with_enums(Contract((), tuple(declarations), ()))
    canonical_values = {
        declaration.name
        for declaration in contract.declarations
        if isinstance(declaration.type, StringEnum)
    }
    python = target.language == "python"
    models = (
        _Models(contract.declarations, field_name=model_field).render()
        if python
        else generate_typescript(contract)["types.ts"]
    )
    if not python:
        from fastapi_canon.integrations.sse.codegen.typescript import _render_type

        for model in plan.models:
            if not model.problem_type:
                continue
            fields = []
            required = model.schema.get("required", [])
            for name, schema in sorted(model.schema.get("properties", {}).items()):
                annotation = _render_type(types.parse(schema))
                annotation = annotation.replace("\n", "\n  ")
                fields.append(
                    f"  {json.dumps(name)}{'' if name in required else '?'}: {annotation};"
                )
            span = _typescript_declaration_span(models, model.name)
            if span:
                start, end = span
                models = (
                    models[:start]
                    + f"export interface {model.name} extends Problem {{\n"
                    + "\n".join(fields)
                    + "\n}"
                    + models[end:]
                )
        if any(model.problem_type for model in plan.models):
            models = 'import type { Problem } from "./errors.js";\n' + models
    output = {
        "models.py" if python else "models.ts": models,
        "errors.py" if python else "errors.ts": _errors(plan, target),
    }
    canonical = {model.name for model in plan.models}
    problem_errors = {
        model.name: _error_name(model.name)
        for model in plan.models
        if model.problem_type
    }
    if len(set(problem_errors.values())) != len(problem_errors) or set(
        problem_errors.values()
    ) & (
        canonical
        | {
            "HttpError",
            "ProblemError",
            "ProtocolError",
            "TransportError",
            target.client_name,
        }
    ):
        raise GenerationError(
            "A public problem error name collides with another model or error"
        )
    for source in plan.sources:
        if source.kind == "openapi":
            if not any(
                operation.binding.protocol == "sse" for operation in source.operations
            ):
                continue
            from fastapi_canon.integrations.sse.codegen.diagnostics import (
                GenerationDiagnostic,
            )
            from fastapi_canon.integrations.sse.codegen.ir import build_ir

            found: list[GenerationDiagnostic] | None = (
                [] if diagnostics is not None else None
            )
            ir = build_ir(source.document, target=target.language, diagnostics=found)
            if diagnostics is not None and found:
                diagnostics.extend(
                    _GenerationWarning(
                        f"{item.keyword}: {item.message}",
                        source.name,
                        item.operation_id,
                        f"{item.method} {item.path}",
                        target.name,
                        item.pointer,
                    )
                    for item in found
                )
            # Only an actual stream creates an SSE binding/runtime.
            if not ir.operations:
                continue
            backend = (
                {
                    "runtime.py": sse_runtime(http_client=target.http_client),
                    "__init__.py": "",
                }
                if python
                else generate_typescript(ir)
            )
            prefix = f"internal/{source.name}_sse"
        elif source.kind == "openrpc":
            from fastapi_canon.integrations.websockets.codegen import (
                PythonClientOptions,
                TypeScriptClientOptions,
            )
            from fastapi_canon.integrations.websockets.codegen import build_ir as rpc_ir
            from fastapi_canon.integrations.websockets.codegen.python import (
                render_files as rpc_python,
            )
            from fastapi_canon.integrations.websockets.codegen.typescript import (
                render_files as rpc_typescript,
            )

            prefix = f"internal/{source.name}_jsonrpc"
            if python:
                backend = rpc_python(
                    _binding_ir(rpc_ir(source.document)),
                    PythonClientOptions(
                        package=target.package + "." + prefix.replace("/", "."),
                        client_name="BindingClient",
                        with_transport="websocket",
                    ),
                )
            else:
                backend = rpc_typescript(
                    _binding_ir(rpc_ir(source.document)),
                    TypeScriptClientOptions(
                        client_name="BindingClient", with_transport="websocket"
                    ),
                )
        else:
            from fastapi_canon.integrations.webrtc.codegen.typescript import (
                generate_typescript as generate,
            )

            prefix = f"internal/{source.name}_webrtc"
            backend = generate(source.document, client_name="BindingClient")
        for path, content in backend.items():
            if python:
                content = _python_backend(
                    content, path, prefix, target.package, canonical, problem_errors
                )
            else:
                content = _typescript_backend(
                    content, path, prefix, canonical, problem_errors, canonical_values
                )
            if path in {
                "problem.py",
                "internal/problem.py",
                "problem.ts",
                "transport/problem.ts",
            }:
                continue
            output[prefix + "/" + path] = content
    output.update(
        _python_client(plan, target) if python else _typescript_client(plan, target)
    )
    if python:
        output["internal/__init__.py"] = ""
        output["py.typed"] = ""
    runtime = (
        files("fastapi_canon.codegen.templates")
        .joinpath("client.py" if python else "client.ts")
        .read_text(encoding="utf-8")
    )
    if python:
        runtime = runtime.replace(
            "import httpx\n", f"import {target.http_client} as httpx\n"
        )
    if python:
        runtime = runtime.replace("from .problem import", "from ..errors import")
    output["internal/runtime.py" if python else "internal/runtime.ts"] = runtime
    if python:
        output = _python_sources(output)
    return output


def _python_sources(output: dict[str, str]) -> dict[str, str]:
    # Format the package in two processes, independent of its module count.
    with TemporaryDirectory(prefix="canon-python-") as directory:
        root = Path(directory)
        paths = [path for path in output if path.endswith(".py")]
        for path in paths:
            module = root / path
            module.parent.mkdir(parents=True, exist_ok=True)
            module.write_text(output[path], encoding="utf-8")
        for arguments in (
            ["check", "--select", "F401,I", "--fix"],
            ["format"],
        ):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    *arguments,
                    "--isolated",
                    "--target-version",
                    "py312",
                    directory,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode:
                raise GenerationError(
                    "Cannot format generated Python SDK; install fastapi-canon[codegen]"
                )
        return output | {
            path: (root / path).read_text(encoding="utf-8") for path in paths
        }


def _errors(plan: Plan, target: TargetConfig) -> str:
    runtime = problem_runtime(target.language)
    problems = [model for model in plan.models if model.problem_type]
    if target.language != "python":
        classes = "\n".join(
            f"export class {_error_name(model.name)} extends ProblemError<Models.{model.name}> {{\n"
            f"  override readonly name = {json.dumps(_error_name(model.name))};\n"
            "}\n"
            for model in problems
        )
        bindings = ",\n".join(
            f"  {json.dumps(model.problem_type)}: {_error_name(model.name)}"
            for model in problems
        )
        model_import = 'import type * as Models from "./models.js";' if problems else ""
        return (
            runtime
            + f"""
{model_import}

export class HttpError<T = unknown> extends TransportError {{
  override readonly name = "HttpError";
  declare readonly details: T;
  declare readonly response: Response;
  constructor(readonly status: number, details: T, response: Response, options?: ErrorOptions) {{
    super(`HTTP ${{status}}`, options);
    Object.defineProperties(this, {{ details: {{ value: details }}, response: {{ value: response }} }});
  }}
}}

{classes}
const problemTypes: Readonly<Record<string, new (problem: any) => ProblemError>> = {{
{bindings}
}};

export function problemError(value: unknown, embedded = false): ProblemError {{
  const problem = parseProblem(value, embedded);
  const ErrorType = problemTypes[problem.type] ?? ProblemError;
  return new ErrorType(problem);
}}
"""
        )
    runtime = runtime.replace("def parse_problem(", "def _parse_problem(")
    runtime = runtime.replace("def problem_error(", "def _basic_problem_error(")
    bindings = ",\n".join(
        f"    {model.problem_type!r}: TypeAdapter(Models.{model.name})"
        for model in plan.models
        if model.problem_type
    )
    return (
        runtime
        + f"""
from pydantic import TypeAdapter, ValidationError
from . import models as Models

{"".join(f"class {_error_name(model.name)}(ProblemError[Models.{model.name}]):\n    problem_adapter = TypeAdapter(Models.{model.name})\n\n\n" for model in problems)}
_PROBLEM_ERRORS: dict[str, type[ProblemError[Any]]] = {{
{",".join(f"{model.problem_type!r}: {_error_name(model.name)}" for model in problems)}
}}

_PROBLEM_MODELS: dict[str, TypeAdapter[Any]] = {{
{bindings}
}}

def parse_problem(value: Any, *, embedded: bool = False) -> Any:
    parsed = _parse_problem(value, embedded=embedded)
    adapter = _PROBLEM_MODELS.get(parsed.type)
    if adapter is None:
        return parsed
    try:
        return adapter.validate_python(value)
    except ValidationError as exc:
        raise ProtocolError("Invalid declared problem payload") from exc


def problem_error(value: Any, *, embedded: bool = False) -> ProblemError[Any]:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True, exclude_unset=True)
    problem = parse_problem(value, embedded=embedded)
    return _PROBLEM_ERRORS.get(problem.type, ProblemError)(problem)
"""
    )


def _alias(operation: Operation, name: str) -> str:
    return "".join(pascal(part) for part in operation.name) + pascal(name)


def _error_name(model: str) -> str:
    return model.removesuffix("Problem").removesuffix("Error") + "Error"


def _python_backend(
    content: str,
    path: str,
    prefix: str,
    package: str,
    canonical: set[str],
    problem_errors: dict[str, str],
) -> str:
    tree = ast.parse(content)
    lines = content.splitlines(keepends=True)
    edits: list[tuple[int, int, str]] = []
    base = package + "." + prefix.replace("/", ".")
    module = base + ("." + path[:-3].replace("/", ".") if path != "__init__.py" else "")
    parent = (
        module.removesuffix(".__init__")
        if path.endswith("__init__.py")
        else module.rsplit(".", 1)[0]
    )
    for node in tree.body:
        if (
            prefix.endswith("_sse")
            and path == "runtime.py"
            and isinstance(node, ast.ClassDef)
            and node.name == "HttpError"
        ):
            edits.append(
                (
                    node.lineno,
                    node.end_lineno or node.lineno,
                    f"from {package}.errors import HttpError\n",
                )
            )
        if path == "errors.py" and isinstance(node, ast.ClassDef):
            for base_type in node.bases:
                if (
                    isinstance(base_type, ast.Subscript)
                    and isinstance(base_type.value, ast.Name)
                    and base_type.value.id == "ProblemError"
                    and isinstance(base_type.slice, ast.Name)
                    and base_type.slice.id in problem_errors
                ):
                    edits.append(
                        (
                            node.lineno,
                            node.end_lineno or node.lineno,
                            f"from {package}.errors import {problem_errors[base_type.slice.id]} as {node.name}\n",
                        )
                    )
        if isinstance(node, ast.ImportFrom):
            resolved = node.module or ""
            if node.level:
                parts = parent.split(".")
                resolved = ".".join(
                    [*parts[: len(parts) - node.level + 1], *resolved.split(".")]
                ).rstrip(".")
            if resolved in {base + ".problem", base + ".internal.problem"}:
                names = ", ".join(
                    alias.name + (f" as {alias.asname}" if alias.asname else "")
                    for alias in node.names
                )
                edits.append(
                    (
                        node.lineno,
                        node.end_lineno or node.lineno,
                        f"from {package}.errors import {names}\n",
                    )
                )
        if path == "models.py":
            name = (
                node.name.id
                if isinstance(node, ast.TypeAlias)
                else node.target.id
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                else node.name
                if isinstance(node, ast.ClassDef)
                else node.targets[0].id
                if isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name)
                else None
            )
            if name in canonical:
                edits.append(
                    (
                        node.lineno,
                        node.end_lineno or node.lineno,
                        f"from {package}.models import {name} as {name}\n",
                    )
                )
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start - 1 : end] = [replacement]
    content = "".join(lines)
    if prefix.endswith("_jsonrpc") and path == "client.py":
        content = _python_connect_options(content)
    if prefix.endswith("_sse") and path == "runtime.py":
        content = content.replace(
            "raise ProblemError(problem)", "raise problem_error(problem)"
        )
        content = (
            f"from {package}.errors import problem_error\n" + content
            if "from __future__" not in content
            else content.replace(
                "from __future__ import annotations",
                f"from __future__ import annotations\nfrom {package}.errors import problem_error",
            )
        )
    return re.sub(
        rf"\bfrom {re.escape(package)}\.",
        "from " + "." * (len(parent.split(".")) - len(package.split(".")) + 1),
        content,
    )


def _python_connect_options(content: str) -> str:
    """Derive option types from the binding's actual connection signature."""
    for node in ast.walk(ast.parse(content)):
        if isinstance(node, ast.FunctionDef) and node.name == "connect":
            fields = [
                f"    {argument.arg}: {ast.unparse(argument.annotation)}"
                for argument in node.args.kwonlyargs
                if argument.annotation is not None
            ]
            if "from __future__ import annotations" in content:
                content = content.replace(
                    "from __future__ import annotations",
                    "from __future__ import annotations\nfrom typing import TypedDict",
                    1,
                )
            else:
                content = "from typing import TypedDict\n" + content
            return (
                content
                + "\n\nclass _ConnectOptions(TypedDict, total=False):\n"
                + "\n".join(fields)
                + "\n"
            )
    raise GenerationError("The RPC binding is missing its connect signature")


def _typescript_backend(
    content: str,
    path: str,
    prefix: str,
    canonical: set[str],
    problem_errors: dict[str, str],
    canonical_values: set[str],
) -> str:
    if prefix.endswith("_sse") and path == "transport/errors.ts":
        content = re.sub(
            r"export class HttpError<T = unknown> extends TransportError \{.*?\n\}",
            'import { HttpError } from "../../../errors.js";\nexport { HttpError };',
            content,
            flags=re.DOTALL,
        )
    if path == "errors.ts" and prefix.endswith("_jsonrpc"):
        for model, error in problem_errors.items():
            replacement = f'import {{ {error} as \\1 }} from "../../errors.js";\nexport {{ \\1 }};'
            content = re.sub(
                rf"export class (\w+) extends ProblemError<{re.escape(model)}> \{{[^}}]*\}}",
                replacement,
                content,
            )
    if prefix.endswith("_sse") and path == "transport/sse.ts":
        content = 'import { problemError } from "../../../errors.js";\n' + content
        content = content.replace(
            "new ProblemError(parseProblem(value, true))", "problemError(value, true)"
        ).replace("new ProblemError(problem)", "problemError(problem)")
    if prefix.endswith("_webrtc") and path == "runtime.ts":
        content = (
            'import { TransportError } from "../../errors.js";\n'
            + content.replace(
                "class RtcError extends Error", "class RtcError extends TransportError"
            )
        )

    def module(match: re.Match[str]) -> str:
        value = match.group(2)
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(path), value)
        ).removesuffix(".js")
        if resolved not in {"problem", "transport/problem"}:
            return match.group(0)
        relative = posixpath.relpath(
            "errors.js", posixpath.dirname(prefix + "/" + path)
        )
        return match.group(1) + json.dumps(relative) + match.group(3)

    content = re.sub(r'(\bfrom\s+)["\']([^"\']+)["\'](;?)', module, content)
    if path in {"models.ts", "types.ts"}:
        # Canonical declarations are imported once; binding-specific event/error aliases stay private.
        for name in sorted(canonical):
            found = False
            while True:
                span = _typescript_declaration_span(content, name)
                if span is None:
                    break
                found = True
                start, end = span
                content = content[:start] + content[end:]
            relative = posixpath.relpath(
                "models.js", posixpath.dirname(prefix + "/" + path)
            )
            if found:
                import_kind = "" if name in canonical_values else "type "
                content = (
                    f'import {import_kind}{{ {name} }} from "{relative}";\nexport {import_kind}{{ {name} }};\n'
                    + content
                )
    if path == "errors.ts" and prefix.endswith("_jsonrpc"):
        content = _typescript_type_imports(content)
    return content


def _typescript_type_imports(content: str) -> str:
    pattern = r"import type \{([^}]+)\} from ([^;]+);"
    body = re.sub(pattern, "", content)

    def used(match: re.Match[str]) -> str:
        names = [name.strip() for name in match.group(1).split(",") if name.strip()]
        names = [name for name in names if re.search(rf"\b{re.escape(name)}\b", body)]
        return (
            f"import type {{ {', '.join(names)} }} from {match.group(2)};"
            if names
            else ""
        )

    return re.sub(pattern, used, content)


def _typescript_client(plan: Plan, target: TargetConfig) -> dict[str, str]:
    runtime_imports = ["Runtime", "type ClientOptions"]
    if any(operation.interaction == "call" for operation in plan.operations):
        runtime_imports.append("operation")
    if any(operation.binding.protocol == "http" for operation in plan.operations):
        runtime_imports.append("type HttpOptions")
    if any(operation.binding.protocol == "sse" for operation in plan.operations):
        runtime_imports.append("type StreamOptions")
    if any(
        operation.binding.protocol in {"jsonrpc", "websocket"}
        and operation.interaction != "session"
        for operation in plan.operations
    ):
        runtime_imports.append("type CallOptions")
    imports = [
        'import type * as Models from "./models.js";',
        "import { " + ", ".join(runtime_imports) + ' } from "./internal/runtime.js";',
    ]
    factories = []
    options = []
    for source in plan.sources:
        if source.kind == "openapi":
            if not any(
                operation.binding.protocol == "sse" for operation in source.operations
            ):
                continue
            imports.append(
                f'import {{ createClient as {source.name}Factory }} from "./internal/{source.name}_sse/client.js";'
            )
            factories.append(
                f"{json.dumps(source.name + ':sse')}: {{ create: () => {source.name}Factory(this.runtime.httpOptions()), owned: true }},"
            )
        elif source.kind == "openrpc":
            imports.append(
                f'import {{ BindingClient as {source.name}Client }} from "./internal/{source.name}_jsonrpc/client.js";'
            )
            options.append(
                f"{source.name}?: {{ client?: {source.name}Client; owned?: boolean; connect?: Parameters<typeof {source.name}Client.connect>[0] }};"
            )
            servers = source.document.get("servers", [])
            defaults = (
                f"url: this.runtime.websocketUrl({json.dumps(urlsplit(servers[0]['url']).path)}), "
                if len(servers) == 1 and "{" not in servers[0]["url"]
                else ""
            )
            if source.document.get("x-canon-application") and any(
                "baseUrl" in server.get("variables", {}) for server in servers
            ):
                defaults = (
                    'baseUrl: this.runtime.websocketUrl("").replace(/\\/$/, ""), '
                )
            factories.append(
                f"{json.dumps(source.name)}: {{ create: () => {source.name}Client.connect({{lazy: true, {defaults}...options.bindings?.{source.name}?.connect, signal: this.runtime.bindingSignal(options.bindings?.{source.name}?.connect?.signal)}}), client: options.bindings?.{source.name}?.client, owned: options.bindings?.{source.name}?.owned ?? !options.bindings?.{source.name}?.client }},"
            )
        else:
            imports.append(
                f'import {{ BindingClient as {source.name}Client }} from "./internal/{source.name}_webrtc/client.js";'
            )
            options.append(
                f"{source.name}?: {{ config?: ConstructorParameters<typeof {source.name}Client>[0] }};"
            )
            factories.append(
                f"{json.dumps(source.name)}: {{ create: () => new {source.name}Client({{baseUrl: options.baseUrl, ...options.bindings?.{source.name}?.config}}), owned: false }},"
            )
    methods: dict[tuple[str, ...], str] = {}
    for operation in plan.operations:
        name = tuple(camel(part) for part in operation.name)
        fields = "\n".join(
            f"  {camel(param.name)}{'' if param.required else '?'}: {_parameter_type(operation, param, 'typescript')};"
            for param in operation.parameters
        )
        params = f"params: {{\n{fields}\n}}" + (
            " = {}" if not any(param.required for param in operation.parameters) else ""
        )
        signature = params + ", " if operation.parameters else ""
        binding = operation.binding
        result = _public_type(
            operation.result, "Models." + _alias(operation, "Result"), "typescript"
        )
        if binding.protocol == "http":
            bodyless = binding.metadata["bodyless"]
            responses = [
                (status, schema)
                for status, schema in binding.metadata["responses"].items()
                if status not in bodyless
            ]
            if responses:
                result = " | ".join(
                    dict.fromkeys(
                        _public_type(
                            schema,
                            "Models."
                            + _alias(
                                operation,
                                "Response" + status
                                if binding.metadata.get("binary")
                                else "Result",
                            ),
                            "typescript",
                        )
                        for status, schema in responses
                    )
                )
            if len(bodyless) == len(binding.metadata["responses"]):
                result = "void"
            elif bodyless:
                result += " | void"
        elif operation.result == {"type": "null"}:
            result = "void"
        if binding.protocol == "http":
            spec = {
                "path": binding.path,
                "method": binding.method,
                "statuses": list(binding.metadata["responses"]),
                "bodyless": bodyless,
                "binary": binding.metadata.get("binary", []),
                "requestMedia": binding.metadata.get("request_media", ""),
                "parameters": [
                    {
                        "name": camel(param.name),
                        "wire": param.wire_name,
                        "location": param.location,
                        "required": param.required,
                        "array": param.schema.get("type") == "array",
                    }
                    for param in operation.parameters
                ],
            }
            values = "params" if operation.parameters else "{}"
            body = f"this.runtime.request<{result}>(\n{indent(json.dumps(spec, indent=2), '  ')},\n  {values},\n  options,\n)"
            method = f"({signature}options?: HttpOptions): Promise<{result}> =>\n{indent(body, '  ')}"
        else:
            binding_source = binding.source + (
                ":sse" if binding.protocol == "sse" else ""
            )
            if binding.protocol == "sse":
                path = list(binding.metadata["backend_typescript"])
                args = [
                    "{ "
                    + ", ".join(
                        _typescript_argument(
                            binding.metadata["typescript_arguments"][param.name],
                            param.name,
                            param.required,
                        )
                        for param in operation.parameters
                    )
                    + " }",
                    "options",
                ]
                if not operation.parameters:
                    args = ["options"]
            elif binding.protocol == "webrtc":
                path = list(binding.backend_path)
                backend = (
                    binding_source
                    + "Client"
                    + "".join(f"[{json.dumps(part)}]" for part in path)
                )
                methods[name] = (
                    f"(options: Parameters<{backend}>[0]): ReturnType<{backend}> => this.runtime.session(async () => (await this.runtime.binding<{binding_source}Client>({json.dumps(binding_source)})){_access(path)}(options))"
                )
                continue
            else:
                path = list(binding.metadata["backend_typescript"])
                backend = (
                    binding_source
                    + "Client"
                    + "".join(f"[{json.dumps(part)}]" for part in path)
                )
                args = [
                    "{ "
                    + ", ".join(
                        _typescript_argument(
                            param.wire_name, param.name, param.required
                        )
                        for param in operation.parameters
                    )
                    + " }"
                ]
                if not operation.parameters:
                    args = []
                if operation.interaction == "call" or binding.metadata.get(
                    "subscription"
                ):
                    args.append("options")
            binding_type = (
                f"ReturnType<typeof {binding.source}Factory>"
                if binding.protocol == "sse"
                else binding_source + "Client"
            )
            arguments = indent(",\n".join(args), "  ")
            invoke = f"(await this.runtime.binding<{binding_type}>(\n  {json.dumps(binding_source)},\n)){_access(path)}(\n{arguments}\n)"
            if operation.interaction == "stream" and binding.metadata.get(
                "subscription"
            ):
                method = f"({signature}options?: CallOptions): ReturnType<{backend}> =>\n  this.runtime.session(async () =>\n{indent(invoke, '    ')}\n  )"
            elif operation.interaction == "stream":
                stream_options = (
                    "StreamOptions"
                    if binding.protocol == "sse"
                    else "Pick<CallOptions, 'signal'>"
                )
                method = f"({signature}options?: {stream_options}): AsyncIterable<{result}> =>\n  this.runtime.stream(\n    async () =>\n{indent(invoke, '      ')},\n    options?.signal,\n  )"
            elif operation.interaction == "session":
                method = f"(...args: Parameters<{backend}>): ReturnType<{backend}> => this.runtime.session(async () => (await this.runtime.binding<{binding_source}Client>({json.dumps(binding_source)})){_access(path)}(...args))"
            else:
                call = invoke.replace("\n  options\n)", "\n  requestOptions\n)")
                method = f"({signature}options?: CallOptions): Promise<{result}> =>\n  this.runtime.call(\n    async requestOptions =>\n{indent(call, '      ')},\n    options,\n  )"
        if operation.interaction == "call":
            problems = (
                " | ".join("Models." + problem for problem in operation.problems)
                or "never"
            )
            types = [
                model.problem_type
                for model in plan.models
                if model.name in operation.problems and model.problem_type
            ]
            method = f"operation<{problems}>({json.dumps(types)})(\n{indent(method, '  ')}\n)"
        methods[name] = _typescript_doc(operation) + method
    body = _tree_ts(methods)
    client = (
        "\n".join(imports)
        + f"\n\nexport interface Options extends ClientOptions {{\n  bindings?: {{\n    {' '.join(options)}\n  }};\n}}\nexport class {target.client_name} {{\n  private readonly runtime: Runtime;\n  constructor(options: Options) {{\n    this.runtime = new Runtime(options);\n    this.runtime.register({{\n      {''.join(factories)}\n    }});\n  }}\n  close(): Promise<void> {{ return this.runtime.close(); }}\n  async [Symbol.asyncDispose](): Promise<void> {{ await this.close(); }}\n{body}\n}}\nexport function createClient(options: Options): {target.client_name} {{\n  return new {target.client_name}(options);\n}}\n"
    )
    used = sorted(set(re.findall(r"Models\.(\w+)", client)))
    client = client.replace(
        'import type * as Models from "./models.js";',
        "import type { " + ", ".join(used) + ' } from "./models.js";' if used else "",
    )
    client = client.replace("Models.", "")
    return {
        "client.ts": client,
        "index.ts": 'export * from "./client.js";\n'
        + "export { ProblemError, TransportError, ProtocolError, HttpError"
        + "".join(
            ", " + _error_name(model.name)
            for model in plan.models
            if model.problem_type
        )
        + ' } from "./errors.js";\nexport type { Problem } from "./errors.js";\n'
        + "".join(
            f'export {"" if "enum" in model.schema else "type "}{{ {model.name} }} from "./models.js";\n'
            for model in plan.models
            if model.name in _public_model_names(plan)
        )
        + 'export type { CallOptions, HttpOptions, StreamOptions, OperationResult } from "./internal/runtime.js";\n',
    }


def _typescript_argument(wire: str, name: str, required: bool) -> str:
    value = "params." + camel(name)
    field = f"{json.dumps(wire)}: {value}"
    return field if required else f"...({value} === undefined ? {{}} : {{ {field} }})"


def _public_model_names(plan: Plan) -> set[str]:
    from fastapi_canon.codegen.selection import _references

    models = {model.name: model.schema for model in plan.models}
    names: set[str] = set()
    prefix = "#/components/schemas/"
    for operation in plan.operations:
        names.update(_references(operation.result, prefix))
        names.update(
            _references(operation.binding.metadata.get("messages", {}), prefix)
        )
        for parameter in operation.parameters:
            names.update(_references(parameter.schema, prefix))
        names.update(operation.problems)
    pending = list(names)
    while pending:
        schema = models.get(pending.pop(), {})
        children = _references(schema, prefix) - names
        names.update(children)
        pending.extend(children)
    return names & models.keys()


def _typescript_declaration_span(content: str, name: str) -> tuple[int, int] | None:
    match = re.search(
        rf"export (?:interface|type|const) {re.escape(name)}(?:\s|=|\{{)", content
    )
    if match is None:
        return None
    depth = 0
    quote = ""
    escape = False
    for end in range(match.start(), len(content)):
        char = content[end]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == ";" and depth == 0:
            return match.start(), end + 1
        if (
            char == "}"
            and depth == 0
            and content[match.start() :].startswith("export interface")
        ):
            return match.start(), end + 1
    raise GenerationError(f"Unterminated generated declaration {name!r}")


def _access(path: list[str]) -> str:
    return "".join(f"[{json.dumps(part)}]" for part in path)


def _public_type(schema: dict[str, Any], fallback: str, language: str) -> str:
    if schema.get("x-canon-binary"):
        return "bytes" if language == "python" else "globalThis.Blob"
    branches = schema.get("anyOf", [])
    if any(branch.get("x-canon-binary") for branch in branches):
        return " | ".join(
            _public_type(branch, fallback, language) for branch in branches
        )
    reference = schema.get("$ref", "")
    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
        return "Models." + reference.rsplit("/", 1)[-1]
    primitives = (
        {
            "string": "str",
            "integer": "int",
            "number": "float",
            "boolean": "bool",
            "null": "None",
        }
        if language == "python"
        else {
            "string": "string",
            "integer": "number",
            "number": "number",
            "boolean": "boolean",
            "null": "null",
        }
    )
    kind = schema.get("type")
    if (
        isinstance(kind, str)
        and kind in primitives
        and "enum" not in schema
        and "const" not in schema
    ):
        return primitives[kind]
    # Complex types are already represented by complete canonical declarations.
    # Projecting them a second time can lose maps, literal values or constraints.
    return fallback


def _parameter_type(operation: Operation, parameter: Parameter, language: str) -> str:
    if parameter.location == "file":
        value = "Upload" if language == "python" else "globalThis.Blob"
        if parameter.schema.get("type") == "array":
            value = f"list[{value}]" if language == "python" else f"{value}[]"
    else:
        value = _public_type(
            parameter.schema, "Models." + _alias(operation, parameter.name), language
        )
    if parameter.location in {"file", "form"} and not parameter.required:
        value += " | None" if language == "python" else " | null"
    return value


def _typescript_doc(operation: Operation) -> str:
    lines = [operation.summary, operation.description]
    if operation.deprecated:
        lines.append("@deprecated This operation is deprecated by the server contract.")
    lines.extend(
        f'@throws {{import("./errors.js").{_error_name(name)}}} Declared public problem.'
        for name in operation.problems
    )
    text = "\n\n".join(line for line in lines if line)
    if not text:
        return ""
    return (
        "/**\n"
        + "\n".join(" * " + line for line in text.replace("*/", "* /").splitlines())
        + "\n */\n"
    )


def _tree_ts(methods: dict[tuple[str, ...], str]) -> str:
    tree: dict[str, Any] = {}
    for path, method in methods.items():
        node = tree
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = method

    def render_node(node: dict[str, Any], root: bool = False) -> str:
        lines = []
        for key, value in node.items():
            doc = ""
            if isinstance(value, str) and value.startswith("/**"):
                doc, value = value.split(" */\n", 1)
                doc += " */\n"
            rendered = (
                "{\n" + render_node(value) + "\n}" if isinstance(value, dict) else value
            )
            rendered = (
                doc
                + f"{'readonly ' if root else ''}{key} {'=' if root else ':'} "
                + rendered
                + (";" if root else ",")
            )
            lines.append("\n".join("  " + line for line in rendered.splitlines()))
        return "\n".join(lines)

    return render_node(tree, True)


def _python_client(plan: Plan, target: TargetConfig) -> dict[str, str]:
    imports = [
        "from __future__ import annotations",
        "from collections.abc import AsyncIterator",
        "from contextlib import AbstractAsyncContextManager",
        "from typing import Any, Self, TypedDict",
        f"import {target.http_client} as Http",
        "from . import models as Models",
        "from .internal.runtime import Runtime, RequestOptions, UNSET, UnsetType",
    ]
    uploads = any(
        param.location == "file"
        for operation in plan.operations
        for param in operation.parameters
    )
    if uploads:
        imports.append("from .internal.runtime import Upload")
    registrations = []
    binding_fields = []
    client_fields = []
    binding_exports = []
    for source in plan.sources:
        if source.kind == "openapi":
            if any(
                operation.binding.protocol == "sse" for operation in source.operations
            ):
                imports.append(
                    f"from .internal.{source.name}_sse import runtime as {source.name}Sse"
                )
                registrations.append(
                    f"self._runtime.register('{source.name}:sse', lambda: {source.name}Sse.Transport(base_url, http_client=self._runtime.http), owned=True)"
                )
        else:
            imports.append(
                f"from .internal.{source.name}_jsonrpc.client import BindingClient as {source.name}Client"
            )
            option_type = pascal(source.name) + "ConnectionOptions"
            imports.append(
                f"from .internal.{source.name}_jsonrpc.client import _ConnectOptions as {option_type}"
            )
            binding_fields.append(f"    {source.name}: {option_type}")
            client_fields.append(f"    {source.name}: {source.name}Client")
            binding_exports.append(option_type)
            if any(
                operation.binding.metadata.get("backend_type") == "socket"
                for operation in source.operations
            ):
                imports.append(
                    f"from .internal.{source.name}_jsonrpc import sockets as {source.name}Sockets"
                )
            if any(
                operation.binding.metadata.get("backend_type") == "binary"
                for operation in source.operations
            ):
                imports.append(
                    f"from .internal.{source.name}_jsonrpc import streams as {source.name}Streams"
                )

            servers = source.document.get("servers", [])
            defaults = (
                {"url": urlsplit(servers[0]["url"]).path}
                if len(servers) == 1 and "{" not in servers[0]["url"]
                else {}
            )
            default_options = (
                "{'url': self._runtime.websocket_url(" + repr(defaults["url"]) + ")}"
                if defaults
                else "{}"
            )
            if source.document.get("x-canon-application") and any(
                "baseUrl" in server.get("variables", {}) for server in servers
            ):
                default_options = (
                    "{'base_url': self._runtime.websocket_url('').rstrip('/')}"
                )
            registrations.append(
                f"self._runtime.register('{source.name}', lambda: {source.name}Client.connect(**{{'lazy': True, **{default_options}, **(bindings or {{}}).get('{source.name}', {{}})}}), client=(clients or {{}}).get('{source.name}'), owned='{source.name}' not in (clients or {{}}))"
            )
    groups = {
        operation.name[:index]
        for operation in plan.operations
        for index in range(1, len(operation.name))
    }
    classes = [
        "class Bindings(TypedDict, total=False):\n"
        + ("\n".join(binding_fields) or "    pass"),
        "class Clients(TypedDict, total=False):\n"
        + ("\n".join(client_fields) or "    pass"),
    ]
    for group in sorted(groups, key=lambda path: (-len(path), path)):
        lines = [
            f"class {_group_class(group)}:",
            "    def __init__(self, runtime: Runtime) -> None:",
            "        self._runtime = runtime",
        ]
        for child in sorted(groups):
            if len(child) == len(group) + 1 and child[:-1] == group:
                lines.append(
                    f"        self.{snake(child[-1])} = {_group_class(child)}(runtime)"
                )
        for operation in plan.operations:
            if operation.name[:-1] == group:
                lines.extend(_python_method(operation))
        classes.append("\n".join(lines))
    lines = [
        f"class {target.client_name}:",
        "    def __init__(self, base_url: str, *, headers: dict[str, str] | None = None, http_client: Http.AsyncClient | None = None, bindings: Bindings | None = None, clients: Clients | None = None) -> None:",
        "        self._runtime = Runtime(base_url, headers=headers, http_client=http_client)",
    ]
    lines.extend("        " + registration for registration in registrations)
    for group in sorted(groups):
        if len(group) == 1:
            lines.append(
                f"        self.{snake(group[0])} = {pascal(group[0])}Api(self._runtime)"
            )
    for operation in plan.operations:
        if len(operation.name) == 1:
            lines.extend(_python_method(operation))
    lines.extend(
        [
            "    async def aclose(self) -> None:",
            "        await self._runtime.aclose()",
            "    async def __aenter__(self) -> Self:",
            "        return self",
            "    async def __aexit__(self, *args: Any) -> None:",
            "        await self.aclose()",
        ]
    )
    classes.append("\n".join(lines))
    return {
        "client.py": "\n".join(imports) + "\n\n" + "\n\n".join(classes) + "\n",
        "__init__.py": f"from .client import {target.client_name} as {target.client_name}, Bindings as Bindings, Clients as Clients\n"
        + "".join(f"from .client import {name} as {name}\n" for name in binding_exports)
        + "from .errors import Problem as Problem, ProblemError as ProblemError, TransportError as TransportError, ProtocolError as ProtocolError, HttpError as HttpError\nfrom .internal.runtime import RequestOptions as RequestOptions\n"
        + ("from .internal.runtime import Upload as Upload\n" if uploads else "")
        + "".join(
            f"from .errors import {_error_name(model.name)} as {_error_name(model.name)}\n"
            for model in plan.models
            if model.problem_type
        ),
    }


def _group_class(path: tuple[str, ...]) -> str:
    return "_".join(pascal(part) for part in path) + "Api"


def _python_method(operation: Operation) -> list[str]:
    lines = _python_method_body(operation)
    documentation = "\n\n".join(
        value for value in (operation.summary, operation.description) if value
    )
    if operation.problems:
        documentation += "\n\nRaises:\n" + "\n".join(
            f"    {_error_name(name)}: Declared public problem."
            for name in operation.problems
        )
    if documentation:
        lines.insert(1, "        " + repr(documentation.strip()))
    return lines


def _python_method_body(operation: Operation) -> list[str]:
    binding = operation.binding
    args = ", ".join(
        f"{snake(param.name)}: {_parameter_type(operation, param, 'python')}"
        + (" | UnsetType = UNSET" if not param.required else "")
        for param in operation.parameters
    )
    args = (
        ", *, "
        + (args + ", " if args else "")
        + "options: RequestOptions | None = None"
    )
    result = _public_type(
        operation.result, "Models." + _alias(operation, "Result"), "python"
    )
    params = (
        "{"
        + ", ".join(
            f"{param.wire_name!r}: {snake(param.name)}"
            for param in operation.parameters
        )
        + "}"
    )
    if binding.protocol == "http":
        if binding.metadata.get("binary"):
            result = " | ".join(
                dict.fromkeys(
                    _public_type(
                        schema,
                        "Models." + _alias(operation, "Response" + status),
                        "python",
                    )
                    for status, schema in binding.metadata["responses"].items()
                )
            )
        spec = {
            "path": binding.path,
            "method": binding.method,
            "statuses": list(binding.metadata["responses"]),
            "bodyless": binding.metadata["bodyless"],
            "binary": binding.metadata.get("binary", []),
            "request_media": binding.metadata.get("request_media", ""),
            "parameters": [
                {
                    "wire": param.wire_name,
                    "location": param.location,
                    "required": param.required,
                    "array": param.schema.get("type") == "array",
                }
                for param in operation.parameters
            ],
        }
        response_types = (
            "{"
            + ", ".join(
                f"{status!r}: Models.{_alias(operation, 'Response' + status)}"
                for status in binding.metadata["responses"]
                if status not in binding.metadata["bodyless"]
            )
            + "}"
        )
        return [
            f"    async def {snake(operation.name[-1])}(self{args}) -> {result}:",
            f"        return await self._runtime.request({spec!r}, {params}, {response_types}, options, {{"
            + ", ".join(
                f"{param.wire_name!r}: Models.{_alias(operation, param.name)}"
                for param in operation.parameters
            )
            + "})",
        ]
    source = binding.source + (":sse" if binding.protocol == "sse" else "")
    path = binding.metadata["backend_python"]
    call = "client." + ".".join(path)
    kwargs = (
        "**{name: value for name, value in {"
        + ", ".join(
            f"{binding.metadata['python_arguments'][param.name]!r}: {snake(param.name)}"
            for param in operation.parameters
        )
        + "}.items() if value is not UNSET}"
    )
    if binding.protocol == "sse":
        kwargs += (", " if kwargs else "") + "options=options"
    if operation.interaction == "call":
        return [
            f"    async def {snake(operation.name[-1])}(self{args}) -> {result}:",
            f"        return await self._runtime.call({source!r}, {tuple(path)!r}, {{"
            + ", ".join(
                f"{binding.metadata['python_arguments'][param.name]!r}: {snake(param.name)}"
                for param in operation.parameters
            )
            + "}, options)",
        ]
    if operation.interaction == "stream":
        if binding.metadata.get("subscription"):
            values = (
                "{"
                + ", ".join(
                    f"{binding.metadata['python_arguments'][param.name]!r}: {snake(param.name)}"
                    for param in operation.parameters
                )
                + "}"
            )
            return [
                f"    def {snake(operation.name[-1])}(self{args}) -> AbstractAsyncContextManager[AsyncIterator[{result}]]:",
                f"        return self._runtime.message_stream({source!r}, {tuple(path)!r}, {values}, {result})",
            ]
        if binding.protocol == "sse":
            spec = {
                "path": binding.path,
                "method": binding.method,
                "canon_stream": binding.metadata["profile"],
                "parameters": [
                    {
                        "wire": param.wire_name,
                        "location": param.location,
                        "required": param.required,
                    }
                    for param in operation.parameters
                ],
            }
            parameter_types = (
                "{"
                + ", ".join(
                    f"{param.wire_name!r}: Models.{_alias(operation, param.name)}"
                    for param in operation.parameters
                )
                + "}"
            )
            setup = f"        stream = await self._runtime.open_stream({source!r}, {spec!r}, {params}, {result}, {parameter_types}, options, {binding.source}Sse.RequestOptions)"
        else:
            setup = f"        client = await self._runtime.binding({source!r})\n        stream = {call}({kwargs})"
        return [
            f"    async def {snake(operation.name[-1])}(self{args}) -> AsyncIterator[{result}]:",
            setup,
            "        async with self._runtime.iterating(stream) as stream:",
            "            async for item in stream:",
            f"                yield self._runtime.result(item, {result})",
        ]
    if binding.metadata["backend_type"] == "binary":
        session_type = binding.source + "Streams." + binding.metadata["python_session"]
    elif binding.metadata.get("messages"):
        session_type = (
            binding.source
            + "Sockets.TypedSocket[Models."
            + _alias(operation, "client")
            + ", Models."
            + _alias(operation, "server")
            + "]"
        )
    else:
        session_type = binding.source + "Sockets.Socket"
    args = (
        ", *, "
        + ", ".join(
            f"{snake(param.name)}: Models.{_alias(operation, param.name)} | UnsetType = UNSET"
            for param in operation.parameters
        )
        if operation.parameters
        else ""
    )
    return [
        f"    def {snake(operation.name[-1])}(self{args}) -> AbstractAsyncContextManager[{session_type}]:",
        f"        return self._runtime.session({source!r}, {tuple(path)!r}, {{"
        + ", ".join(
            f"{binding.metadata['python_arguments'][param.name]!r}: {snake(param.name)}"
            for param in operation.parameters
        )
        + "})",
    ]


def _binding_ir(ir: Any) -> Any:
    from fastapi_canon.integrations.websockets.codegen.ir import (
        AliasDecl,
        EnumDecl,
        EnumLiteralType,
        LiteralType,
        Primitive,
        PrimitiveType,
        UnionType,
    )

    values = {
        (enum.name, member.name): member.value
        for enum in ir.declarations
        if isinstance(enum, EnumDecl)
        for member in enum.members
    }

    def project(value: Any) -> Any:
        if isinstance(value, EnumLiteralType):
            return LiteralType(values[(value.enum, value.member)])
        if isinstance(value, PrimitiveType) and value.primitive in {
            Primitive.UUID,
            Primitive.DATETIME,
        }:
            return PrimitiveType(Primitive.STRING)
        if isinstance(value, EnumDecl) and any(
            not isinstance(member.value, str) for member in value.members
        ):
            return AliasDecl(
                value.name,
                UnionType(tuple(LiteralType(member.value) for member in value.members)),
            )
        if isinstance(value, tuple):
            return tuple(project(item) for item in value)
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items()}
        if is_dataclass(value) and not isinstance(value, type):
            return replace(
                value,
                **{
                    field.name: project(getattr(value, field.name))
                    for field in fields(value)
                    if field.init
                },
            )
        return value

    return project(ir)
