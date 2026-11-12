"""Lexical ownership of imperative transport registrations; source never executes."""

import ast
from dataclasses import dataclass

from fastapi_canon.checks.source import _Source
from fastapi_canon.checks.symbols import _Definition

_ROUTERS = frozenset(
    {
        "fastapi_canon.APIRouter",
        "fastapi_canon.api_router.APIRouter",
        "fastapi_canon.router.CanonRouter",
    }
)
_REGISTRATIONS = frozenset({"method", "stream", "socket", "binary_stream"})


@dataclass(frozen=True)
class _Owner:
    kind: str
    layers: tuple[tuple[str, ast.Call], ...]
    status: str = "supported"


@dataclass(frozen=True)
class _Decorator:
    module: str
    call: ast.Call
    owners: tuple[_Owner, ...]


type _Symbol = str | _Owner | _Decorator | _Definition | None
type _Bindings = dict[str, tuple[_Symbol, ...]]


@dataclass(frozen=True)
class _Registration:
    module: str
    call: ast.Call
    decorator: _Decorator
    function: _Definition | None
    uncertain: bool


class _Registrations:
    def __init__(self, source: _Source) -> None:
        self.source = source
        self.functions = {
            id(d.node): d
            for d in [*source.nested, *source.definitions.values()]
            if isinstance(d.node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        self.result: list[_Registration] = []

    def read(self) -> list[_Registration]:
        for module, body in self.source.bodies.items():
            self._block(body, module, {}, False)
        return self.result

    def _block(
        self, body: list[ast.stmt], module: str, env: _Bindings, uncertain: bool
    ) -> None:
        for node in body:
            match node:
                case ast.Import() | ast.ImportFrom():
                    for alias in node.names:
                        name = alias.asname or alias.name.partition(".")[0]
                        # The source index already resolves relative imports and
                        # explicit re-exports. Bind the reference, never import it.
                        env[name] = (self.source.canonical(f"{module}.{name}"),)
                case ast.FunctionDef() | ast.AsyncFunctionDef():
                    definition = self.functions[id(node)]
                    for decorator in node.decorator_list:
                        if isinstance(decorator, ast.Call) and isinstance(
                            decorator.func, ast.Attribute
                        ):
                            owner_symbols = self._symbols(
                                decorator.func.value, module, env
                            )
                            owners = tuple(
                                s for s in owner_symbols if isinstance(s, _Owner)
                            )
                            if owners and decorator.func.attr in _REGISTRATIONS:
                                self.result.append(
                                    _Registration(
                                        module,
                                        decorator,
                                        _Decorator(module, decorator, owners),
                                        definition,
                                        uncertain or len(owner_symbols) != 1,
                                    )
                                )
                    env[node.name] = (definition,)
                    local = dict(env)
                    scope = self.source.lexical.get(id(node))
                    if scope:
                        for name in scope.local_names:
                            local[name] = (None,)
                    self._block(node.body, module, local, True)
                case ast.ClassDef():
                    env[node.name] = (f"{module}.{node.name}",)
                    self._block(node.body, module, dict(env), True)
                case ast.Assign() | ast.AnnAssign():
                    if node.value is None:
                        continue
                    if isinstance(node.value, ast.Call):
                        self._call(node.value, module, env, uncertain)
                    symbols = self._symbols(node.value, module, env)
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in targets:
                        if isinstance(target, ast.Name):
                            env[target.id] = symbols
                case ast.Expr() if isinstance(node.value, ast.Call):
                    self._call(node.value, module, env, uncertain)
                case ast.If():
                    left, right = dict(env), dict(env)
                    self._block(node.body, module, left, True)
                    self._block(node.orelse, module, right, True)
                    self._merge(env, [left, right])
                case ast.While() | ast.For() | ast.AsyncFor():
                    local = dict(env)
                    if isinstance(node, ast.For | ast.AsyncFor):
                        for stored in ast.walk(node.target):
                            if isinstance(stored, ast.Name):
                                local[stored.id] = (None,)
                    self._block(node.body, module, local, True)
                    self._merge(env, [dict(env), local])
                    self._block(node.orelse, module, env, True)
                case ast.With() | ast.AsyncWith():
                    self._block(node.body, module, env, True)
                case ast.Try() | ast.TryStar():
                    branches = []
                    for statements in [node.body, *(h.body for h in node.handlers)]:
                        local = dict(env)
                        self._block(statements, module, local, True)
                        branches.append(local)
                    self._merge(env, branches)
                    self._block(node.orelse, module, env, True)
                    self._block(node.finalbody, module, env, True)
                case ast.Match():
                    branches = [dict(env)]
                    for case in node.cases:
                        local = dict(env)
                        self._block(case.body, module, local, True)
                        branches.append(local)
                    self._merge(env, branches)

    def _call(
        self, call: ast.Call, module: str, env: _Bindings, uncertain: bool
    ) -> None:
        if not call.args:
            return
        decorators = self._symbols(call.func, module, env)
        if isinstance(call.func, ast.Attribute) and call.func.attr in _REGISTRATIONS:
            owners = tuple(
                s
                for s in self._symbols(call.func.value, module, env)
                if isinstance(s, _Owner) and s.kind == "rpc"
            )
            _, argument = self.source.value(call.args[0], module)
            if owners and not isinstance(argument, ast.Constant):
                decorators = (_Decorator(module, call, owners),)
        functions = self._symbols(call.args[0], module, env)
        definitions = []
        for symbol in functions:
            definition = (
                self.source.definitions.get(symbol)
                if isinstance(symbol, str)
                else symbol
            )
            if isinstance(definition, _Definition) and isinstance(
                definition.node, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                definitions.append(definition)
        for decorator in decorators:
            if isinstance(decorator, _Decorator):
                self.result.append(
                    _Registration(
                        module,
                        call,
                        decorator,
                        definitions[0] if len(definitions) == 1 else None,
                        uncertain or len(decorators) != 1 or len(functions) != 1,
                    )
                )

    def _symbols(
        self,
        node: ast.expr,
        module: str,
        env: _Bindings,
        seen: frozenset[tuple[str, int]] = frozenset(),
    ) -> tuple[_Symbol, ...]:
        key = module, id(node)
        if key in seen:
            return (None,)
        seen = seen | {key}
        if isinstance(node, ast.Name) and node.id in env:
            return env[node.id]
        if isinstance(node, ast.Call):
            names = self._symbols(node.func, module, env, seen)
            result: list[_Symbol] = []
            for name in names:
                if isinstance(name, str):
                    status = self.source.rpc_channel(name)
                    if name in _ROUTERS:
                        result.append(_Owner("router", ((module, node),)))
                    elif status is not None:
                        result.append(_Owner("rpc", ((module, node),), status))
                elif (
                    isinstance(name, _Definition)
                    and isinstance(name.node, ast.FunctionDef | ast.AsyncFunctionDef)
                    and name.node.returns
                ):
                    returned = self.source.annotation(name.node.returns, name.module)
                    if returned in _ROUTERS:
                        result.append(_Owner("router", ((module, node),), "incomplete"))
                    elif self.source.rpc_channel(returned) is not None:
                        result.append(_Owner("rpc", ((module, node),), "incomplete"))
            if isinstance(node.func, ast.Attribute):
                owners = self._symbols(node.func.value, module, env, seen)
                for owner in owners:
                    if not isinstance(owner, _Owner):
                        continue
                    method = node.func.attr
                    if method == "child" and owner.kind == "rpc":
                        result.append(
                            _Owner("rpc", (*owner.layers, (module, node)), owner.status)
                        )
                    elif method == "channel" and owner.kind == "router":
                        result.append(_Owner("rpc", ((module, node),), "incomplete"))
                    elif method in _REGISTRATIONS:
                        result.append(_Decorator(module, node, (owner,)))
            return tuple(dict.fromkeys(result)) or (None,)
        scope, value = self.source.value(node, module)
        if value is not node:
            return self._symbols(value, scope, {}, seen)
        name = self.source.resolve(node, module)
        return (name,) if name else (None,)

    def _merge(self, env: _Bindings, branches: list[_Bindings]) -> None:
        for name in set().union(*(branch.keys() for branch in branches)):
            symbols: list[_Symbol] = []
            for branch in branches:
                for symbol in branch.get(name, (None,)):
                    if symbol not in symbols:
                        symbols.append(symbol)
            env[name] = tuple(symbols)
