"""Read Canon declarations and compare them with explicit escaping exceptions."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, NamedTuple

from fastapi_canon.checks.analysis import (
    HTTP_ROUTE_METHODS,
    RPC_METHOD,
    TRANSPORT_METHODS,
    ContractAnalyzer,
    ExceptionFlow,
    UnknownValue,
)
from fastapi_canon.checks.source import SourceDefinition, SourceIndex

_API_ROUTERS = frozenset(
    {"fastapi_canon.APIRouter", "fastapi_canon.api_router.APIRouter"}
)
_CANON_ROUTERS = frozenset({"fastapi_canon.router.CanonRouter", *_API_ROUTERS})
_WEBRTC_ROUTERS = frozenset(
    {
        "fastapi_canon.integrations.webrtc.WebRtcRouter",
        "fastapi_canon.integrations.webrtc.canon.WebRtcRouter",
    }
)
_ERROR = "fastapi_canon.Error"
_ERRORS = frozenset(
    {_ERROR, "fastapi_canon.error.Error", "fastapi_canon.error.error.Error"}
)
_DEPENDENCIES = {
    "fastapi.Depends",
    "fastapi.params.Depends",
    "fastapi.Security",
    "fastapi.params.Security",
}


@dataclass(frozen=True, slots=True)
class _Diagnostic:
    code: str
    message: str
    path: str
    line: int
    trace: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Operation:
    function: str
    path: str
    line: int
    diagnostics: tuple[_Diagnostic, ...] = ()
    transport: str = "http"

    @property
    def complete(self) -> bool:
        return not any(item.code == "CAN003" for item in self.diagnostics)


@dataclass(frozen=True, slots=True)
class ContractReport:
    operations: tuple[_Operation, ...]

    @property
    def gaps(self) -> int:
        return sum(
            item.code != "CAN003"
            for operation in self.operations
            for item in operation.diagnostics
        )

    @property
    def incomplete(self) -> int:
        return sum(not operation.complete for operation in self.operations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operations": [
                {
                    "function": operation.function,
                    "path": operation.path,
                    "line": operation.line,
                    "diagnostics": [asdict(item) for item in operation.diagnostics],
                    "transport": operation.transport,
                    "complete": operation.complete,
                }
                for operation in self.operations
            ],
            "contract_gaps": self.gaps,
            "incomplete_operations": self.incomplete,
        }


def _keyword(node: ast.Call, name: str) -> ast.expr | None:
    return next((item.value for item in node.keywords if item.arg == name), None)


class _CheckContracts:
    def __init__(self, source: SourceIndex) -> None:
        self.source = source
        self._catalog_cache: dict[str, str] | None = None
        # flow-analysis unit seam
        self._analyzer = ContractAnalyzer(source)

    def __call__(self) -> ContractReport:
        operations: list[_CheckOperation] = []
        nested = {id(function.node) for function in self.source.nested}
        for function in [*self.source.iter_definitions(), *self.source.nested]:
            if not isinstance(function.node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in function.node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(
                    decorator.func, ast.Attribute
                ):
                    method, owner = decorator.func.attr, decorator.func.value
                elif isinstance(decorator, ast.Attribute):
                    method, owner = decorator.attr, decorator.value
                else:
                    continue
                if method == RPC_METHOD:
                    rpc = self._rpc_method(function, decorator, owner, nested=nested)
                    if rpc is not None:
                        operations.append(rpc)
                    continue
                if not isinstance(decorator, ast.Call) or method not in {
                    *HTTP_ROUTE_METHODS,
                    *TRANSPORT_METHODS,
                }:
                    continue
                module, router = self.source.value(owner, function.module)
                operation = _CheckOperation(
                    function.name,
                    str(self.source.paths[function.module]),
                    decorator.lineno,
                    transport=TRANSPORT_METHODS.get(method, "http"),
                )
                if id(function.node) in nested:
                    operations.append(operation)
                    self._unknown(
                        operation,
                        "nested route declarations require runtime factory execution",
                    )
                    continue
                constructor = (
                    self.source.resolve(router.func, module)
                    if isinstance(router, ast.Call)
                    else None
                )
                if method == "session":
                    supported = _WEBRTC_ROUTERS
                elif method in TRANSPORT_METHODS:
                    supported = _API_ROUTERS
                else:
                    supported = _CANON_ROUTERS
                if method == "session" and constructor not in supported:
                    # ``session`` is a common method name; only WebRTC routers declare.
                    continue
                if constructor not in supported:
                    if constructor in {
                        "fastapi.APIRouter",
                        "fastapi.routing.APIRouter",
                        "fastapi.FastAPI",
                    }:
                        continue
                    operations.append(operation)
                    self._unknown(
                        operation, "unresolved or unsupported router declaration"
                    )
                    continue
                assert isinstance(router, ast.Call)
                operations.append(operation)
                self._operation(
                    operation, function, decorator, module=module, router=router
                )
        return ContractReport(tuple(operation.snapshot() for operation in operations))

    def _rpc_method(
        self,
        function: SourceDefinition,
        decorator: ast.expr,
        owner: ast.expr,
        *,
        nested: set[int],
    ) -> _CheckOperation | None:
        # `method` is a common attribute name: only resolved RpcChannels count.
        self.source.reset_usage({function.module})
        module, channel = self.source.value(owner, function.module)
        if not isinstance(channel, ast.Call):
            return None
        declaration = self.source.rpc_channel(self.source.resolve(channel.func, module))
        if declaration is None:
            return None
        declaration_modules = frozenset(self.source.touched)
        operation = _CheckOperation(
            function.name,
            str(self.source.paths[function.module]),
            decorator.lineno,
            transport="rpc",
        )
        if id(function.node) in nested:
            self._unknown(
                operation, "nested RPC declarations require runtime factory execution"
            )
            return operation
        if declaration == "incomplete":
            self._unknown(
                operation,
                "RPC channel subclass contracts require runtime class or constructor execution",
            )
            return operation
        call = decorator if isinstance(decorator, ast.Call) else None
        self._operation(
            operation,
            function,
            call,
            module=module,
            router=channel,
            declaration_modules=declaration_modules,
        )
        return operation

    def _operation(
        self,
        operation: _CheckOperation,
        function: SourceDefinition,
        decorator: ast.Call | None,
        *,
        module: str,
        router: ast.Call,
        declaration_modules: frozenset[str] = frozenset(),
    ) -> None:
        self.source.reset_usage({function.module, module, *declaration_modules})
        layers = [(module, router)]
        policy_node = (
            _keyword(router, "policy") if operation.transport != "rpc" else None
        )
        if policy_node is not None:
            scope, policy = self.source.value(policy_node, module)
            if not (isinstance(policy, ast.Constant) and policy.value is None):
                if isinstance(policy, ast.Call) and self.source.resolve(
                    policy.func, scope
                ) in {
                    "fastapi_canon.RouterPolicy",
                    "fastapi_canon.policy.RouterPolicy",
                }:
                    layers.insert(0, (scope, policy))
                else:
                    self._unknown(operation, "unresolved RouterPolicy declaration")
        for scope, line in self.source.inherited_contracts:
            operation.report(
                _Diagnostic(
                    "CAN003",
                    "router contracts inherited through include_router are outside "
                    "source-declaration analysis",
                    str(self.source.paths[scope]),
                    line,
                )
            )
        calls = [layer for _, layer in layers]
        if decorator is not None:
            calls.append(decorator)
        for call in calls:
            if any(item.arg is None for item in call.keywords):
                self._unknown(
                    operation, "expanded keyword arguments may change the contract"
                )
        # Composition maps every declared Error application-wide.
        catalog = dict(self._catalog())
        declared: dict[str, str] = {}
        for scope, layer in layers:
            declared.update(self._errors(_keyword(layer, "raises"), scope, operation))
        if decorator is not None:
            declared.update(
                self._errors(_keyword(decorator, "raises"), function.module, operation)
            )
        catalog.update(declared)
        contracts_known = not operation.diagnostics
        # SSE generator bodies run while the response streams; follow iterated calls.
        flow = self._analyzer.function(
            function,
            iterated=operation.transport == "sse",
            dependency_transport=operation.transport,
        )
        flow.extend(self._dependencies(function, transport=operation.transport))
        for scope, layer in layers:
            flow.extend(self._router_dependencies(layer, scope, operation))
        if decorator is not None:
            flow.extend(
                self._router_dependencies(decorator, function.module, operation)
            )
        contracts_known = contracts_known and not any(
            self.source.uncertainties(scope) for scope in self.source.touched
        )
        self._compare(
            operation, flow, catalog, declared=declared, contracts_known=contracts_known
        )
        for scope in sorted(self.source.touched):
            for message in dict.fromkeys(self.source.uncertainties(scope)):
                operation.report(
                    _Diagnostic("CAN003", message, str(self.source.paths[scope]), 1)
                )

    def _catalog(self) -> dict[str, str]:
        if self._catalog_cache is None:
            touched = set(self.source.touched)
            catalog: dict[str, str] = {}
            for module, node in self.source.error_declarations:
                scope, sequence = self.source.value(node, module)
                if not isinstance(sequence, ast.List | ast.Tuple):
                    continue
                for element in sequence.elts:
                    mapped = self._mapped_error(element, scope)
                    if mapped is not None:
                        catalog[mapped.exception] = mapped.error
            self.source.reset_usage(touched)
            self._catalog_cache = catalog
        return self._catalog_cache

    def _mapped_error(self, element: ast.expr, module: str) -> _ErrorMapping | None:
        identity = self.source.resolve(element, module)
        scope, definition = self.source.value(element, module)
        if (
            identity is None
            or not isinstance(definition, ast.Call)
            or self.source.resolve(definition.func, scope) not in _ERRORS
        ):
            return None
        exception_node = (
            definition.args[0] if definition.args else _keyword(definition, "exception")
        )
        exception = (
            self.source.resolve(exception_node, scope) if exception_node else None
        )
        if exception is None or not self.source.exception(exception):
            return None
        return _ErrorMapping(exception, identity)

    def _errors(
        self, node: ast.expr | None, module: str, operation: _CheckOperation
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for scope, element in self._sequence(node, module, operation):
            identity = self.source.resolve(element, scope)
            scope, definition = self.source.value(element, scope)
            if (
                not isinstance(definition, ast.Call)
                or self.source.resolve(definition.func, scope) not in _ERRORS
            ):
                self._unknown(
                    operation, f"unresolved Error definition: {ast.unparse(element)}"
                )
                continue
            exception_node = (
                definition.args[0]
                if definition.args
                else _keyword(definition, "exception")
            )
            exception = (
                self.source.resolve(exception_node, scope) if exception_node else None
            )
            if (
                identity is None
                or exception is None
                or not self.source.exception(exception)
            ):
                self._unknown(
                    operation, f"unresolved Error exception: {ast.unparse(element)}"
                )
                continue
            result[exception] = identity
        return result

    def _router_dependencies(
        self, node: ast.Call, module: str, operation: _CheckOperation
    ) -> ExceptionFlow:
        flow = ExceptionFlow()
        for scope, dependency in self._sequence(
            _keyword(node, "dependencies"), module, operation
        ):
            # Reuse the parameter-dependency reader with a synthetic annotated argument.
            argument = ast.arg(arg="dependency", annotation=dependency)
            function = ast.FunctionDef(
                name="dependencies",
                args=ast.arguments(
                    posonlyargs=[],
                    args=[argument],
                    kwonlyargs=[],
                    kw_defaults=[],
                    defaults=[],
                ),
                body=[],
                decorator_list=[],
                type_params=[],
            )
            function.lineno = operation.line
            flow.extend(
                self._dependencies(
                    SourceDefinition(f"{scope}.dependencies", scope, function),
                    transport=operation.transport,
                )
            )
        return flow

    def _sequence(
        self,
        node: ast.expr | None,
        module: str,
        operation: _CheckOperation,
        *,
        seen: frozenset[int] = frozenset(),
    ) -> list[tuple[str, ast.expr]]:
        if node is None:
            return []
        module, node = self.source.value(node, module)
        if id(node) in seen:
            self._unknown(operation, "recursive contract sequence")
            return []
        seen = seen | {id(node)}
        if isinstance(node, ast.List | ast.Tuple | ast.Set):
            result: list[tuple[str, ast.expr]] = []
            for element in node.elts:
                if isinstance(element, ast.Starred):
                    result.extend(
                        self._sequence(element.value, module, operation, seen=seen)
                    )
                else:
                    result.append((module, element))
            return result
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._sequence(
                node.left, module, operation, seen=seen
            ) + self._sequence(node.right, module, operation, seen=seen)
        self._unknown(operation, f"unresolved contract sequence: {ast.unparse(node)}")
        return []

    def _unknown(
        self, operation: _CheckOperation, message: str, line: int | None = None
    ) -> None:
        operation.report(
            _Diagnostic(
                "CAN003",
                message,
                operation.path,
                line if line is not None else operation.line,
            )
        )

    def _dependencies(
        self,
        function: SourceDefinition,
        seen: frozenset[str] = frozenset(),
        *,
        transport: str | None = None,
    ) -> ExceptionFlow:
        flow = ExceptionFlow()
        if function.name in seen:
            flow.add_unknown(
                UnknownValue(
                    "recursive dependency", function.name, function.node.lineno
                )
            )
            return flow
        node = function.node
        assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        values: list[ast.expr] = [arg.annotation for arg in args if arg.annotation]
        values.extend(node.args.defaults)
        values.extend(value for value in node.args.kw_defaults if value is not None)
        for expression in values:
            if isinstance(expression, ast.Constant) and isinstance(
                expression.value, str
            ):
                try:
                    expression = ast.parse(expression.value, mode="eval").body
                except SyntaxError:
                    flow.add_unknown(
                        UnknownValue(
                            "unresolved dependency annotation",
                            function.name,
                            node.lineno,
                        )
                    )
                    continue
            module, expression = self.source.value(expression, function.module)
            for child in ast.walk(expression):
                if (
                    not isinstance(child, ast.Call)
                    or self.source.resolve(child.func, module) not in _DEPENDENCIES
                ):
                    continue
                target = child.args[0] if child.args else _keyword(child, "dependency")
                name = self.source.resolve(target, module) if target else None
                dependency = self.source.definition(name or "")
                if dependency and isinstance(
                    dependency.node, ast.FunctionDef | ast.AsyncFunctionDef
                ):
                    flow.extend(
                        self._analyzer.function(
                            dependency, dependency_transport=transport
                        )
                    )
                    flow.extend(
                        self._dependencies(
                            dependency, seen | {function.name}, transport=transport
                        )
                    )
                elif target is not None and self.source.origin(target, module):
                    # Library dependencies such as HTTPBearer() raise no
                    # application exceptions and declare no application
                    # sub-dependencies.
                    continue
                else:
                    flow.add_unknown(
                        UnknownValue(
                            "unresolved FastAPI dependency", function.name, child.lineno
                        )
                    )
        return flow

    def _compare(
        self,
        operation: _CheckOperation,
        flow: ExceptionFlow,
        catalog: dict[str, str],
        *,
        declared: dict[str, str],
        contracts_known: bool,
    ) -> None:
        seen: set[_Finding] = set()
        for escape in flow.escapes:
            if self.source.framework_exception(escape.exception):
                if self.source.websocket_exception(
                    escape.exception
                ) and operation.transport in {"http", "sse", "webrtc"}:
                    origin = self.source.definitions[escape.trace[-1]]
                    operation.report(
                        _Diagnostic(
                            "CAN003",
                            "WebSocketException may escape an HTTP operation; transport narrowing is unresolved",
                            str(self.source.paths[origin.module]),
                            escape.line,
                            escape.trace,
                        )
                    )
                continue
            # Only application-defined exceptions are in scope, not implicit runtime failures.
            if escape.exception.startswith("builtins."):
                continue
            if not contracts_known:
                continue
            mapped = next(
                (
                    catalog[base]
                    for base in self.source.mro(escape.exception) or []
                    if base in catalog
                ),
                None,
            )
            code = "CAN001" if mapped else "CAN002"
            if mapped and mapped in declared.values():
                continue
            key = _Finding(code, escape.exception)
            if key in seen:
                continue
            seen.add(key)
            message = (
                f"{escape.exception}: {mapped} is missing from raises"
                if mapped
                else f"{escape.exception}: no Error mapping in raises or the application catalog"
            )
            origin = self.source.definitions[escape.trace[-1]]
            operation.report(
                _Diagnostic(
                    code,
                    message,
                    str(self.source.paths[origin.module]),
                    escape.line,
                    escape.trace,
                )
            )
        for unknown in flow.unknowns:
            unknown_origin = self.source.definition(unknown.function)
            path = (
                str(self.source.paths[unknown_origin.module])
                if unknown_origin
                else operation.path
            )
            diagnostic = _Diagnostic(
                "CAN003", unknown.message, path, unknown.line, (unknown.function,)
            )
            if diagnostic not in operation.diagnostics:
                operation.report(diagnostic)


def analyze_error_contracts(roots: list[Path]) -> ContractReport:
    return _CheckContracts(SourceIndex.read(roots))()


class _ErrorMapping(NamedTuple):
    exception: str
    error: str


class _Finding(NamedTuple):
    code: str
    exception: str


class _CheckOperation:
    def __init__(
        self, function: str, path: str, line: int, *, transport: str = "http"
    ) -> None:
        self.function = function
        self.path = path
        self.line = line
        self.transport = transport
        self._diagnostics: list[_Diagnostic] = []

    @property
    def diagnostics(self) -> tuple[_Diagnostic, ...]:
        return tuple(self._diagnostics)

    def report(self, diagnostic: _Diagnostic) -> None:
        self._diagnostics.append(diagnostic)

    def snapshot(self) -> _Operation:
        return _Operation(
            self.function, self.path, self.line, self.diagnostics, self.transport
        )
