"""Read Canon declarations and compare them with explicit escaping exceptions."""

import ast
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastapi_canon.checks.analysis import (
    _ROUTE_METHODS,
    _RPC_METHOD,
    _TRANSPORT_METHODS,
    _Analyzer,
)
from fastapi_canon.checks.declarations import _Registration, _Registrations
from fastapi_canon.checks.effects import _Flow, _Unknown
from fastapi_canon.checks.source import _Source
from fastapi_canon.checks.symbols import _Definition
from fastapi_canon.checks.value_flow import _ValueSummary

_API_ROUTERS = frozenset(
    {"fastapi_canon.APIRouter", "fastapi_canon.api_router.APIRouter"}
)
_CANON_ROUTERS = frozenset({"fastapi_canon.router.CanonRouter", *_API_ROUTERS})
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
_RPC_DECLARATIONS = frozenset({_RPC_METHOD, "stream", "socket"})
_SOCKET_DECLARATIONS = frozenset({"socket", "binary_stream"})

type _OperationKey = tuple[
    str,
    int,
    str,
    str,
    str,
    int,
    int,
    tuple[tuple[str, int], ...],
    bool,
    str | None,
    frozenset[str],
]


@dataclass(frozen=True)
class _Channel:
    layers: tuple[tuple[str, ast.Call], ...]
    status: str


@dataclass(frozen=True)
class _Diagnostic:
    code: str
    message: str
    path: str
    line: int
    trace: tuple[str, ...] = ()


@dataclass(frozen=True)
class _OperationSummary:
    diagnostics: tuple[_Diagnostic, ...]
    scheduled: tuple[str, ...]
    observations: _ValueSummary


@dataclass
class _Operation:
    function: str
    path: str
    line: int
    diagnostics: list[_Diagnostic] = field(default_factory=list)
    transport: str = "http"
    scheduled_executions: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not any(item.code == "CAN003" for item in self.diagnostics)


@dataclass
class _Report:
    operations: list[_Operation]

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
                {**asdict(operation), "complete": operation.complete}
                for operation in self.operations
            ],
            "contract_gaps": self.gaps,
            "incomplete_operations": self.incomplete,
        }


def _keyword(node: ast.Call, name: str) -> ast.expr | None:
    return next((item.value for item in node.keywords if item.arg == name), None)


class _Checker:
    def __init__(self, source: _Source) -> None:
        self.source = source
        self._registrations = _Registrations(source).read()
        self._catalog_cache: dict[str, str] | None = None
        # flow-analysis unit seam
        self._analyzer = _Analyzer(source)
        self._operation_summaries: dict[_OperationKey, _OperationSummary] = {}

    def check(self) -> _Report:
        # Operations share application-scoped objects. A later operation can
        # populate a container read by an earlier one; report the fixed point.
        for _ in range(32):
            heap = dict(self.source.heap)
            report = self._check_pass()
            if self.source.heap == heap:
                return report
        for operation in report.operations:
            self._unknown(operation, "shared object flow did not converge")
        return report

    def _check_pass(self) -> _Report:
        operations: list[_Operation] = []
        nested = {id(function.node) for function in self.source.nested}
        for function in [*self.source.definitions.values(), *self.source.nested]:
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
                if method in _RPC_DECLARATIONS:
                    rpc = self._rpc_method(function, decorator, owner, nested, method)
                    if rpc is not None:
                        operations.append(rpc)
                    continue
                if method in _SOCKET_DECLARATIONS:
                    socket = self._socket(function, decorator, owner)
                    if socket is not None:
                        operations.append(socket)
                    continue
                if not isinstance(decorator, ast.Call) or method not in {
                    *_ROUTE_METHODS,
                    *_TRANSPORT_METHODS,
                }:
                    continue
                module, router = self.source.value(owner, function.module)
                operation = _Operation(
                    function.name,
                    str(self.source.paths[function.module]),
                    decorator.lineno,
                    transport=method if method in _TRANSPORT_METHODS else "http",
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
                supported = (
                    _API_ROUTERS if method in _TRANSPORT_METHODS else _CANON_ROUTERS
                )
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
                self._operation(operation, function, decorator, module, router)
        registered_locations = {
            (operation.path, operation.line) for operation in operations
        }
        for declaration in self._registrations:
            location = (
                str(self.source.paths[declaration.module]),
                declaration.call.lineno,
            )
            if location in registered_locations:
                continue
            registration = self._registration(declaration, nested)
            if registration is not None:
                operations.append(registration)
                registered_locations.add(location)
        return _Report(operations)

    def _rpc_method(
        self,
        function: _Definition,
        decorator: ast.expr,
        owner: ast.expr,
        nested: set[int],
        method: str = _RPC_METHOD,
        declaration_module: str | None = None,
        registered_channel: _Channel | None = None,
        uncertain_registration: bool = False,
    ) -> _Operation | None:
        # `method` is a common attribute name: only resolved RpcChannels count.
        owner_module = declaration_module or function.module
        self.source.touched = {function.module, owner_module}
        declaration = registered_channel or self._channel(owner, owner_module)
        if declaration is None:
            return (
                self._socket(function, decorator, owner, owner_module)
                if method == "socket"
                else None
            )
        declaration_modules = frozenset(self.source.touched)
        operation = _Operation(
            function.name,
            str(self.source.paths[owner_module]),
            decorator.lineno,
            transport="rpc",
        )
        if uncertain_registration:
            self._unknown(
                operation,
                "function-scoped registrations require factory composition analysis",
            )
            return operation
        if id(function.node) in nested:
            self._unknown(
                operation, "nested RPC declarations require runtime factory execution"
            )
            return operation
        if declaration.status == "incomplete":
            self._unknown(
                operation,
                "RPC channel subclass or composition contracts require runtime execution",
            )
            return operation
        if method == "socket":
            self._unknown(
                operation,
                "socket context, rejection and close contracts are unresolved",
            )
            return operation
        call = decorator if isinstance(decorator, ast.Call) else None
        module, channel = declaration.layers[-1]
        self._operation(
            operation,
            function,
            call,
            module,
            channel,
            declaration_modules=declaration_modules,
            ancestors=declaration.layers[:-1],
            iterated=method == "stream",
            decorator_module=owner_module,
        )
        return operation

    def _channel(
        self, node: ast.expr, module: str, seen: frozenset[int] = frozenset()
    ) -> _Channel | None:
        module, node = self.source.value(node, module)
        if not isinstance(node, ast.Call) or id(node) in seen:
            return None
        status = self.source.rpc_channel(self.source.resolve(node.func, module))
        if status is not None:
            return _Channel(((module, node),), status)
        if not isinstance(node.func, ast.Attribute):
            return None
        if node.func.attr == "child":
            parent = self._channel(node.func.value, module, seen | {id(node)})
            if parent is not None:
                return _Channel((*parent.layers, (module, node)), parent.status)
        if node.func.attr == "channel" and self._router(node.func.value, module):
            return _Channel(((module, node),), "incomplete")
        return None

    def _router(self, node: ast.expr, module: str) -> bool:
        module, value = self.source.value(node, module)
        return (
            isinstance(value, ast.Call)
            and self.source.resolve(value.func, module) in _CANON_ROUTERS
        )

    def _socket(
        self,
        function: _Definition,
        declaration: ast.expr,
        owner: ast.expr,
        declaration_module: str | None = None,
        registered_owner: bool = False,
    ) -> _Operation | None:
        module = declaration_module or function.module
        if not registered_owner and not self._router(owner, module):
            return None
        operation = _Operation(
            function.name,
            str(self.source.paths[module]),
            declaration.lineno,
            transport="socket",
        )
        self._unknown(
            operation, "socket context, rejection and close contracts are unresolved"
        )
        return operation

    def _registration(
        self, declaration: _Registration, nested: set[int]
    ) -> _Operation | None:
        module, call = declaration.module, declaration.call
        decorator = declaration.decorator.call
        assert isinstance(decorator.func, ast.Attribute)
        method, owner = decorator.func.attr, decorator.func.value
        if declaration.function is None:
            operation = _Operation(
                f"{module}.<registration@{call.lineno}>",
                str(self.source.paths[module]),
                call.lineno,
                transport="socket" if method in _SOCKET_DECLARATIONS else "rpc",
            )
            self._unknown(operation, "unresolved transport handler registration")
            return operation
        function = declaration.function
        owners = declaration.decorator.owners
        if owners[0].kind == "rpc":
            status = "incomplete" if len(owners) != 1 else owners[0].status
            registered = self._rpc_method(
                function,
                decorator,
                owner,
                nested,
                method,
                module,
                registered_channel=_Channel(owners[0].layers, status),
                uncertain_registration=declaration.uncertain,
            )
        else:
            registered = self._socket(
                function, decorator, owner, module, registered_owner=True
            )
        if registered is not None:
            registered.line = call.lineno
        return registered

    def _unknown(
        self, operation: _Operation, message: str, line: int | None = None
    ) -> None:
        operation.diagnostics.append(
            _Diagnostic("CAN003", message, operation.path, line or operation.line)
        )

    def _sequence(
        self,
        node: ast.expr | None,
        module: str,
        operation: _Operation,
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
                        self._sequence(element.value, module, operation, seen)
                    )
                else:
                    result.append((module, element))
            return result
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._sequence(node.left, module, operation, seen) + self._sequence(
                node.right, module, operation, seen
            )
        self._unknown(operation, f"unresolved contract sequence: {ast.unparse(node)}")
        return []

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
                        catalog[mapped[0]] = mapped[1]
            self.source.touched = touched
            self._catalog_cache = catalog
        return self._catalog_cache

    def _mapped_error(self, element: ast.expr, module: str) -> tuple[str, str] | None:
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
        return exception, identity

    def _errors(
        self, node: ast.expr | None, module: str, operation: _Operation
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

    def _dependencies(
        self,
        function: _Definition,
        seen: frozenset[str] = frozenset(),
        *,
        transport: str | None = None,
    ) -> _Flow:
        flow = _Flow()
        if function.name in seen:
            flow.unknowns.append(
                _Unknown("recursive dependency", function.name, function.node.lineno)
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
                    flow.unknowns.append(
                        _Unknown(
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
                dependency = self.source.definitions.get(name or "")
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
                    flow.unknowns.append(
                        _Unknown(
                            "unresolved FastAPI dependency", function.name, child.lineno
                        )
                    )
        return flow

    def _router_dependencies(
        self, node: ast.Call, module: str, operation: _Operation
    ) -> _Flow:
        flow = _Flow()
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
                    _Definition(f"{scope}.dependencies", scope, function),
                    transport=operation.transport,
                )
            )
        return flow

    def _operation(
        self,
        operation: _Operation,
        function: _Definition,
        decorator: ast.Call | None,
        module: str,
        router: ast.Call,
        *,
        declaration_modules: frozenset[str] = frozenset(),
        ancestors: tuple[tuple[str, ast.Call], ...] = (),
        iterated: bool = False,
        decorator_module: str | None = None,
    ) -> None:
        key = (
            operation.path,
            operation.line,
            operation.function,
            operation.transport,
            module,
            id(decorator),
            id(router),
            tuple((scope, id(layer)) for scope, layer in ancestors),
            iterated,
            decorator_module,
            declaration_modules,
        )
        cached = self._operation_summaries.get(key)
        if cached and self.source.reuse(cached.observations):
            operation.diagnostics = list(cached.diagnostics)
            operation.scheduled_executions = list(cached.scheduled)
            return
        with self.source.observations() as observed:
            self._analyze_operation(
                operation,
                function,
                decorator,
                module,
                router,
                declaration_modules=declaration_modules,
                ancestors=ancestors,
                iterated=iterated,
                decorator_module=decorator_module,
            )
        if observed.summary is not None:
            self._operation_summaries[key] = _OperationSummary(
                tuple(operation.diagnostics),
                tuple(operation.scheduled_executions),
                observed.summary,
            )

    def _analyze_operation(
        self,
        operation: _Operation,
        function: _Definition,
        decorator: ast.Call | None,
        module: str,
        router: ast.Call,
        *,
        declaration_modules: frozenset[str],
        ancestors: tuple[tuple[str, ast.Call], ...],
        iterated: bool,
        decorator_module: str | None,
    ) -> None:
        self.source.touched = {function.module, module, *declaration_modules}
        layers = [*ancestors, (module, router)]
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
            operation.diagnostics.append(
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
                self._errors(
                    _keyword(decorator, "raises"),
                    decorator_module or function.module,
                    operation,
                )
            )
        catalog.update(declared)
        contracts_known = not operation.diagnostics
        # SSE generator bodies run while the response streams; follow iterated calls.
        flow = self._analyzer.function(
            function,
            iterated=iterated or operation.transport == "sse",
            dependency_transport=operation.transport,
        )
        flow.extend(self._dependencies(function, transport=operation.transport))
        for scope, layer in layers:
            flow.extend(self._router_dependencies(layer, scope, operation))
        if decorator is not None:
            flow.extend(
                self._router_dependencies(
                    decorator, decorator_module or function.module, operation
                )
            )
        contracts_known = contracts_known and not any(
            self.source.uncertain.get(scope) for scope in self.source.touched
        )
        self._compare(operation, flow, catalog, declared, contracts_known)
        operation.scheduled_executions = sorted(flow.scheduled)
        for scope in sorted(self.source.touched):
            for message in dict.fromkeys(self.source.uncertain.get(scope, [])):
                operation.diagnostics.append(
                    _Diagnostic("CAN003", message, str(self.source.paths[scope]), 1)
                )

    def _compare(
        self,
        operation: _Operation,
        flow: _Flow,
        catalog: dict[str, str],
        declared: dict[str, str],
        contracts_known: bool,
    ) -> None:
        seen: set[tuple[str, str]] = set()
        for escape in flow.escapes:
            if escape.implicit:
                continue
            if self.source.framework_exception(escape.exception):
                if self.source.websocket_exception(
                    escape.exception
                ) and operation.transport in {"http", "sse", "webrtc"}:
                    origin = self.source.definitions[escape.trace[-1]]
                    operation.diagnostics.append(
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
            if escape.exception not in self.source.definitions:
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
            key = code, escape.exception
            if key in seen:
                continue
            seen.add(key)
            message = (
                f"{escape.exception}: {mapped} is missing from raises"
                if mapped
                else f"{escape.exception}: no Error mapping in raises or the application catalog"
            )
            origin = self.source.definitions[escape.trace[-1]]
            operation.diagnostics.append(
                _Diagnostic(
                    code,
                    message,
                    str(self.source.paths[origin.module]),
                    escape.line,
                    escape.trace,
                )
            )
        for unknown in flow.unknowns:
            unknown_origin = self.source.definitions.get(unknown.function)
            path = (
                str(self.source.paths[unknown_origin.module])
                if unknown_origin
                else operation.path
            )
            diagnostic = _Diagnostic(
                "CAN003", unknown.message, path, unknown.line, (unknown.function,)
            )
            if diagnostic not in operation.diagnostics:
                operation.diagnostics.append(diagnostic)


def _check_error_contracts(roots: list[Path]) -> _Report:
    return _Checker(_Source.read(roots)).check()
