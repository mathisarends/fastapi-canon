"""A deliberately bounded Python symbol index; never imports inspected code."""

import ast
import builtins
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self

type _FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
type SymbolEnvironment = dict[str, SymbolValue]
type _FactoryEnvironment = dict[str, tuple[str | None, ...]]

# Builtins whose result or effect depends on application objects at runtime.
_DYNAMIC = frozenset(
    f"builtins.{name}"
    for name in (
        "__import__",
        "aiter",
        "anext",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "iter",
        "locals",
        "next",
        "setattr",
        "super",
        "type",
        "vars",
    )
)
_PROVIDERS = frozenset({"dishka.Provider", "dishka.provider.Provider"})
_PROVIDE = frozenset(
    {
        "dishka.provide",
        "dishka.provider.provide",
        "dishka.alias",
        "dishka.provider.alias",
    }
)
_PROTOCOLS = frozenset({"typing.Protocol", "typing_extensions.Protocol"})
# Bases that add no application methods or exceptions to their subclasses.
_MARKER_BASES = _PROTOCOLS | {"abc.ABC", "typing.Generic", "typing_extensions.Generic"}
_ITERATORS = frozenset(
    f"{module}.{name}"
    for module in ("collections.abc", "typing")
    for name in ("Iterator", "AsyncIterator", "Generator", "AsyncGenerator")
)
_RPC_CHANNELS = frozenset(
    {
        "fastapi_canon.integrations.websockets.RpcChannel",
        "fastapi_canon.integrations.websockets.channel.RpcChannel",
    }
)
_ROUTER_DECLARATIONS = _RPC_CHANNELS | frozenset(
    {
        "fastapi_canon.APIRouter",
        "fastapi_canon.api_router.APIRouter",
        "fastapi_canon.router.CanonRouter",
    }
)
_FACTORY_DECLARATIONS = _ROUTER_DECLARATIONS | {
    "fastapi_canon.Error",
    "fastapi_canon.error.Error",
    "fastapi_canon.error.error.Error",
    "fastapi_canon.RouterPolicy",
    "fastapi_canon.policy.RouterPolicy",
}

_REQUESTS = frozenset(
    {"fastapi.Request", "fastapi.requests.Request", "starlette.requests.Request"}
)
_WEBSOCKETS = frozenset(
    {
        "fastapi.WebSocket",
        "fastapi.websockets.WebSocket",
        "starlette.websockets.WebSocket",
    }
)
_CONNECTIONS = frozenset(
    {"starlette.requests.HTTPConnection", "fastapi.requests.HTTPConnection"}
)
_FRAMEWORK_EXCEPTIONS = {
    "starlette.exceptions.HTTPException": "builtins.Exception",
    "fastapi.exceptions.HTTPException": "starlette.exceptions.HTTPException",
    "starlette.exceptions.WebSocketException": "builtins.Exception",
}
_FRAMEWORK_ALIASES = {
    "fastapi.HTTPException": "fastapi.exceptions.HTTPException",
    "fastapi.WebSocketException": "starlette.exceptions.WebSocketException",
    "fastapi.exceptions.WebSocketException": "starlette.exceptions.WebSocketException",
}
_RESULT_TYPES = frozenset(
    f"{module}.{name}"
    for module, names in (
        ("sqlalchemy.engine", ("Result", "ScalarResult", "MappingResult")),
        ("sqlalchemy.engine.result", ("Result", "ScalarResult", "MappingResult")),
        (
            "sqlalchemy.ext.asyncio",
            ("AsyncResult", "AsyncScalarResult", "AsyncMappingResult"),
        ),
        (
            "sqlalchemy.ext.asyncio.result",
            ("AsyncResult", "AsyncScalarResult", "AsyncMappingResult"),
        ),
    )
    for name in names
)
# Bounded external summaries: an awaited buffered execute returns a sync Result.
# Values extracted from those results may be application objects and stay unknown.
_EXTERNAL_RETURNS = {
    f"{owner}.{method}": (returned, True)
    for owner in (
        "sqlalchemy.ext.asyncio.AsyncSession",
        "sqlalchemy.ext.asyncio.session.AsyncSession",
        "sqlalchemy.ext.asyncio.AsyncConnection",
        "sqlalchemy.ext.asyncio.engine.AsyncConnection",
    )
    for method, returned in (
        ("execute", "sqlalchemy.engine.Result"),
        ("scalars", "sqlalchemy.engine.ScalarResult"),
        ("stream", "sqlalchemy.ext.asyncio.AsyncResult"),
        ("stream_scalars", "sqlalchemy.ext.asyncio.AsyncScalarResult"),
    )
}


def _walk_error(error: OSError) -> None:
    raise error


@dataclass
class SourceDefinition:
    name: str
    module: str
    node: _FunctionNode | ast.ClassDef
    owner: str | None = None


@dataclass(frozen=True)
class SymbolValue:
    name: str = ""
    instance: bool = False
    receiver: str | None = None


@dataclass
class SourceIndex:
    paths: dict[str, Path] = field(default_factory=dict)
    definitions: dict[str, SourceDefinition] = field(default_factory=dict)
    bindings: dict[str, tuple[str, ast.expr] | str] = field(default_factory=dict)
    uncertain: dict[str, list[str]] = field(default_factory=dict)
    nested: list[SourceDefinition] = field(default_factory=list)
    touched: set[str] = field(default_factory=set)
    inherited_contracts: list[tuple[str, int]] = field(default_factory=list)
    # raises= and errors= arguments; Composition maps the errors they name.
    error_declarations: list[tuple[str, ast.expr]] = field(default_factory=list)
    roots: list[Path] = field(default_factory=list)
    _externals: dict[str, bool] = field(default_factory=dict)
    _methods: frozenset[str] | None = None
    _keys: dict[ast.expr, str] = field(default_factory=dict)
    _mros: dict[str, tuple[tuple[str, ...] | None, frozenset[str]]] = field(
        default_factory=dict
    )
    # Port -> (implementation, or None when unresolved; provider module).
    _ports: dict[str, list[tuple[str | None, str]]] | None = None

    @classmethod
    def read(cls, roots: list[Path]) -> Self:
        source = cls()
        calls: list[tuple[str, ast.Call]] = []
        for root in roots:
            root = root.resolve()
            if not root.is_dir():
                raise ValueError(f"source root is not a directory: {root}")
            source.roots.append(root)
            for directory, directories, files in root.walk(on_error=_walk_error):
                directories[:] = sorted(
                    name
                    for name in directories
                    if not name.startswith(".")
                    and name not in {"__pycache__", "node_modules"}
                )
                for filename in sorted(files):
                    if not filename.endswith(".py"):
                        continue
                    path = directory / filename
                    parts = path.relative_to(root).with_suffix("").parts
                    package = filename == "__init__.py"
                    module = ".".join(parts[:-1] if package else parts)
                    if module in source.paths:
                        if source.paths[module] == path:
                            continue
                        raise ValueError(f"duplicate module {module!r}: {path}")
                    source.paths[module] = path
                    tree = ast.parse(path.read_text("utf-8-sig"), filename=str(path))
                    names: set[str] = set()
                    source._index(module, tree.body, package=package, names=names)
                    indexed = {id(source.definitions[name].node) for name in names}
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call):
                            source.error_declarations.extend(
                                (module, item.value)
                                for item in node.keywords
                                if item.arg in {"raises", "errors"}
                            )
                        if isinstance(node, ast.Call) and (
                            (
                                isinstance(node.func, ast.Attribute)
                                and node.func.attr == "include_router"
                            )
                            or any(
                                item.arg == "router_factory" for item in node.keywords
                            )
                        ):
                            calls.append((module, node))
                        elif (
                            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                            and id(node) not in indexed
                        ):
                            source.nested.append(
                                SourceDefinition(
                                    f"{module}.<local>.{node.name}:{node.lineno}",
                                    module,
                                    node,
                                )
                            )
        for module, call in calls:
            if isinstance(call.func, ast.Attribute):
                parent: ast.expr | None = call.func.value
            elif source.resolve(call.func, module) in {
                "fastapi_canon.Composition",
                "fastapi_canon.feature.Composition",
            }:
                parent = _keyword(call, "router_factory")
            else:
                parent = None
            if parent is not None and source._extends_included_routes(
                parent, module, set()
            ):
                source.inherited_contracts.append((module, call.lineno))
        source._index_route_factories()
        return source

    def _extends_included_routes(
        self, node: ast.expr, module: str, seen: set[int]
    ) -> bool:
        # A Canon router's raises, http_statuses and policy extend the
        # operations it includes.
        module, node = self.value(node, module)
        if id(node) in seen:
            return False
        seen.add(id(node))
        if isinstance(node, ast.Lambda):
            return self._extends_included_routes(node.body, module, seen)
        if isinstance(node, ast.Call):
            name = self.resolve(node.func, module)
            keywords = node.keywords
        elif isinstance(node, ast.Name | ast.Attribute):
            # A factory function passed by reference.
            name = self.resolve(node, module)
            keywords = []
        else:
            return False
        if name in _ROUTER_DECLARATIONS:
            return any(
                item.arg is None
                or (
                    item.arg in {"raises", "http_statuses", "policy"}
                    and not (
                        isinstance(item.value, ast.Constant)
                        and item.value.value is None
                    )
                )
                for item in keywords
            )
        definition = self.definitions.get(name) if name is not None else None
        if definition is None or not isinstance(
            definition.node, ast.FunctionDef | ast.AsyncFunctionDef
        ):
            return False
        return any(
            isinstance(statement, ast.Return)
            and statement.value is not None
            and self._extends_included_routes(statement.value, definition.module, seen)
            for statement in ast.walk(definition.node)
        )

    def _index_route_factories(self) -> None:
        for factory in list(self.definitions.values()):
            node = factory.node
            if (
                factory.owner
                or not isinstance(node, ast.FunctionDef)
                or node.decorator_list
                or node.args.posonlyargs
                or node.args.args
                or node.args.kwonlyargs
                or node.args.vararg
                or node.args.kwarg
                or not node.body
                or not isinstance(node.body[-1], ast.Return)
                or not any(
                    isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    for item in node.body
                )
            ):
                continue
            names: list[str] = []
            values: list[ast.expr] = []
            supported = True
            for statement in node.body[:-1]:
                if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                    names.append(statement.name)
                elif isinstance(statement, ast.Assign) and all(
                    isinstance(target, ast.Name) for target in statement.targets
                ):
                    names.extend(
                        target.id
                        for target in statement.targets
                        if isinstance(target, ast.Name)
                    )
                    values.append(statement.value)
                elif (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.value
                ):
                    names.append(statement.target.id)
                    values.append(statement.value)
                elif isinstance(statement, ast.Pass) or (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    continue
                else:
                    supported = False
            returned = node.body[-1].value
            returns = (
                returned.elts
                if isinstance(returned, ast.Tuple | ast.List)
                else [returned]
            )
            if (
                not supported
                or len(names) != len(set(names))
                or not returns
                or not all(isinstance(item, ast.Name) for item in returns)
            ):
                continue
            scope = f"{factory.name}.<locals>"
            self.paths[scope] = self.paths[factory.module]
            for name in self.bindings.keys() | self.definitions.keys():
                parent, _, local = name.rpartition(".")
                if parent == factory.module and local not in names:
                    self.bindings[f"{scope}.{local}"] = name
            indexed: set[str] = set()
            self._index(scope, node.body[:-1], package=False, names=indexed)
            valid_values = all(
                self._route_factory_value(value, scope) for value in values
            )
            for item in returns:
                assert item is not None
                module, value = self.value(item, scope)
                if not isinstance(value, ast.Call) or (
                    self.resolve(value.func, module) not in _ROUTER_DECLARATIONS
                    and self.rpc_channel(self.resolve(value.func, module)) is None
                ):
                    valid_values = False
            if not valid_values:
                self.paths.pop(scope)
                self.bindings = {
                    name: value
                    for name, value in self.bindings.items()
                    if not name.startswith(f"{scope}.")
                }
                self.definitions = {
                    name: definition
                    for name, definition in self.definitions.items()
                    if not name.startswith(f"{scope}.")
                }
                self.uncertain.pop(scope, None)
                continue
            self.uncertain.setdefault(scope, []).extend(
                self.uncertain.get(factory.module, [])
            )
            indexed_nodes = {id(self.definitions[name].node) for name in indexed}
            self.nested = [
                definition
                for definition in self.nested
                if id(definition.node) not in indexed_nodes
            ]

    def _route_factory_value(
        self, node: ast.expr, module: str, seen: frozenset[int] = frozenset()
    ) -> bool:
        if id(node) in seen:
            return False
        seen = seen | {id(node)}
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name | ast.Attribute):
            resolved = self.resolve(node, module)
            if resolved in _FACTORY_DECLARATIONS or resolved in self.definitions:
                return True
            scope, value = self.value(node, module)
            return (value is not node or scope != module) and self._route_factory_value(
                value, scope, seen
            )
        if isinstance(node, ast.Call):
            constructor = self.resolve(node.func, module)
            declaration = (
                constructor in _FACTORY_DECLARATIONS
                or self.rpc_channel(constructor) is not None
            )
            return declaration and all(
                self._route_factory_value(value, module, seen)
                for value in [*node.args, *(item.value for item in node.keywords)]
            )
        if isinstance(node, ast.List | ast.Tuple | ast.Set):
            return all(
                self._route_factory_value(value, module, seen) for value in node.elts
            )
        if isinstance(node, ast.Dict):
            return all(
                self._route_factory_value(value, module, seen)
                for value in [*(key for key in node.keys if key), *node.values]
            )
        if isinstance(node, ast.Starred):
            return self._route_factory_value(node.value, module, seen)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._route_factory_value(
                node.left, module, seen
            ) and self._route_factory_value(node.right, module, seen)
        return False

    def _index(
        self, module: str, body: list[ast.stmt], *, package: bool, names: set[str]
    ) -> None:
        for node in body:
            match node:
                case ast.Import():
                    for alias in node.names:
                        local = alias.asname or alias.name.split(".")[0]
                        self.bindings[f"{module}.{local}"] = (
                            alias.name if alias.asname else local
                        )
                case ast.ImportFrom():
                    prefix = module.split(".") if package else module.split(".")[:-1]
                    if node.level:
                        prefix = prefix[: len(prefix) - node.level + 1]
                        target = ".".join(
                            [*prefix, *([node.module] if node.module else [])]
                        )
                    else:
                        target = node.module or ""
                    for alias in node.names:
                        self.bindings[f"{module}.{alias.asname or alias.name}"] = (
                            f"{target}.{alias.name}"
                        )
                case ast.Assign():
                    for target_node in node.targets:
                        if isinstance(target_node, ast.Name):
                            if f"{module}.{target_node.id}" in self.bindings:
                                self.uncertain.setdefault(module, []).append(
                                    f"reassigned module binding: {target_node.id}"
                                )
                            self.bindings[f"{module}.{target_node.id}"] = (
                                module,
                                node.value,
                            )
                        else:
                            self.uncertain.setdefault(module, []).append(
                                "unsupported module assignment"
                            )
                case ast.AnnAssign() if isinstance(node.target, ast.Name):
                    if node.value is not None:
                        if f"{module}.{node.target.id}" in self.bindings:
                            self.uncertain.setdefault(module, []).append(
                                f"reassigned module binding: {node.target.id}"
                            )
                        self.bindings[f"{module}.{node.target.id}"] = module, node.value
                case ast.TypeAlias() if isinstance(node.name, ast.Name):
                    self.bindings[f"{module}.{node.name.id}"] = module, node.value
                case (
                    ast.AugAssign()
                    | ast.Delete()
                    | ast.For()
                    | ast.While()
                    | ast.Try()
                    | ast.With()
                    | ast.Match()
                ):
                    self.uncertain.setdefault(module, []).append(
                        "dynamic module declarations"
                    )
                case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
                    name = f"{module}.{node.name}"
                    self.definitions[name] = SourceDefinition(name, module, node)
                    names.add(name)
                    if isinstance(node, ast.ClassDef):
                        for member in node.body:
                            if isinstance(
                                member, ast.FunctionDef | ast.AsyncFunctionDef
                            ):
                                key = f"{name}.{member.name}"
                                self.definitions[key] = SourceDefinition(
                                    key, module, member, name
                                )
                                names.add(key)
                case ast.If():
                    # In particular, imports guarded by TYPE_CHECKING remain available.
                    self._index(module, node.body, package=package, names=names)
                    self._index(module, node.orelse, package=package, names=names)
                    if ast.unparse(node.test) not in {
                        "TYPE_CHECKING",
                        "typing.TYPE_CHECKING",
                    }:
                        self.uncertain.setdefault(module, []).append(
                            "conditional module declarations"
                        )
                case ast.Expr() if (
                    isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and (
                        node.value.func.attr
                        in {"append", "extend", "insert", "update", "add_api_route"}
                    )
                ):
                    self.uncertain.setdefault(module, []).append(
                        "dynamic module declarations or contract mutation"
                    )

    def canonical(self, name: str, seen: frozenset[str] = frozenset()) -> str:
        module = name.rpartition(".")[0]
        if module in self.paths:
            self.touched.add(module)
        if name in seen:
            return name
        binding = self.bindings.get(name)
        if isinstance(binding, str):
            return self.canonical(binding, seen | {name})
        if binding is not None and isinstance(binding[1], ast.Name | ast.Attribute):
            return self.resolve(binding[1], binding[0], seen=seen | {name}) or name
        # Package attributes can share a name with a source module. Resolve
        # aliases inside a namespace without rewriting an actual module prefix.
        prefix, separator, member = name.rpartition(".")
        if (
            separator
            and prefix not in self.paths
            and prefix in self.bindings
            and prefix not in seen
        ):
            resolved = self.canonical(prefix, seen | {name})
            if resolved != prefix:
                return self.canonical(f"{resolved}.{member}", seen | {name})
        return _FRAMEWORK_ALIASES.get(name, name)

    def resolve(
        self,
        node: ast.expr,
        module: str,
        env: SymbolEnvironment | None = None,
        *,
        seen: frozenset[str] = frozenset(),
    ) -> str | None:
        self.touched.add(module)
        env = env or {}
        if env:
            text = self.key(node)
            if text in env:
                return env[text].name or None
        if isinstance(node, ast.Name):
            local = f"{module}.{node.id}"
            if local in self.bindings or local in self.definitions:
                return self.canonical(local, seen)
            if hasattr(builtins, node.id):
                return f"builtins.{node.id}"
        elif isinstance(node, ast.Attribute):
            parent = self.resolve(node.value, module, env, seen=seen)
            if parent is None and isinstance(node.value, ast.Call | ast.Await):
                parent = self.infer(node.value, module, env).name
            if parent:
                candidate = self.canonical(f"{parent}.{node.attr}", seen)
                if candidate in self.definitions or parent not in self.definitions:
                    return candidate
                method = self.method(parent, node.attr)
                return method.name if method else candidate
        return None

    def key(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if node not in self._keys:
            self._keys[node] = ast.unparse(node)
        return self._keys[node]

    def value(self, node: ast.expr, module: str) -> tuple[str, ast.expr]:
        seen: set[str] = set()
        while name := self.resolve(node, module):
            if name in seen:
                break
            seen.add(name)
            binding = self.bindings.get(name)
            if binding is None or isinstance(binding, str):
                break
            module, node = binding
        return module, node

    def annotation(
        self, node: ast.expr, module: str, seen: frozenset[int] = frozenset()
    ) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return None
        module, node = self.value(node, module)
        if id(node) in seen:
            return None
        seen = seen | {id(node)}
        if isinstance(node, ast.Subscript):
            wrapper = self.resolve(node.value, module)
            if wrapper in {
                "typing.Annotated",
                "typing_extensions.Annotated",
                "dishka.FromDishka",
                "dishka.integrations.fastapi.FromDishka",
                "fastapi_canon.Inject",
                "fastapi_canon.injection.Inject",
                "fastapi_canon.integrations.websockets.Inject",
            }:
                first = (
                    node.slice.elts[0]
                    if isinstance(node.slice, ast.Tuple)
                    else node.slice
                )
                return self.annotation(first, module, seen)
            return wrapper if wrapper in _RESULT_TYPES else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            # Optional[T] after a guard still has T's methods; absence adds no
            # application-defined explicit exceptions to the supported subset.
            if isinstance(node.right, ast.Constant) and node.right.value is None:
                return self.annotation(node.left, module, seen)
            if isinstance(node.left, ast.Constant) and node.left.value is None:
                return self.annotation(node.right, module, seen)
        return self.resolve(node, module)

    def rpc_channel(
        self, name: str | None
    ) -> Literal["supported", "incomplete"] | None:
        """Recognize RPC ancestry without executing class or constructor code."""
        pending = [name]
        seen: set[str] = set()
        recognized, supported = False, True
        while pending:
            current = pending.pop()
            if current in _RPC_CHANNELS:
                recognized = True
                continue
            if current is None or current in seen:
                supported = False
                continue
            seen.add(current)
            definition = self.definitions.get(current)
            if definition is None or not isinstance(definition.node, ast.ClassDef):
                supported = False
                continue
            self.touched.add(definition.module)
            node = definition.node
            pending.extend(
                self.resolve(
                    base.value if isinstance(base, ast.Subscript) else base,
                    definition.module,
                )
                for base in node.bases
            )
            # Empty single-base classes preserve the constructor and decorator
            # contracts without interpreting application class setup or dispatch.
            if (
                len(node.bases) != 1
                or isinstance(node.bases[0], ast.Subscript)
                or node.decorator_list
                or node.keywords
                or node.type_params
                or not all(
                    isinstance(statement, ast.Pass)
                    or (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str)
                    )
                    for statement in node.body
                )
            ):
                supported = False
        if not recognized:
            return None
        return "supported" if supported else "incomplete"

    def mro(
        self, name: str, seen: frozenset[str] = frozenset()
    ) -> tuple[str, ...] | None:
        if name in seen:
            return None
        if name in self._mros:
            order, modules = self._mros[name]
            self.touched.update(modules)
            return order
        # Resolution also records modules with uncertain declarations. Replay
        # those dependencies on cache hits so completeness checks stay intact.
        previous = self.touched
        self.touched = set()
        try:
            resolved = self._mro(name, seen)
            order = tuple(resolved) if resolved is not None else None
            self._mros[name] = order, frozenset(self.touched)
            return order
        finally:
            previous.update(self.touched)
            self.touched = previous

    def _mro(self, name: str, seen: frozenset[str]) -> list[str] | None:
        if name in _FRAMEWORK_EXCEPTIONS:
            framework_base = _FRAMEWORK_EXCEPTIONS[name]
            order = self.mro(framework_base, seen | {name})
            return [name, *order] if order else None
        if name.startswith("builtins."):
            value = getattr(builtins, name.split(".")[1], None)
            if isinstance(value, type):
                return [f"builtins.{base.__name__}" for base in value.__mro__]
        if name in _MARKER_BASES:
            return [name, "builtins.object"]
        definition = self.definitions.get(name)
        if definition is None or not isinstance(definition.node, ast.ClassDef):
            return None
        bases = [
            # A parametrized generic base has the methods of its origin class.
            self.resolve(
                base.value if isinstance(base, ast.Subscript) else base,
                definition.module,
            )
            for base in definition.node.bases
        ]
        if not bases:
            bases = ["builtins.object"]
        sequences: list[list[str]] = []
        for base in bases:
            order = self.mro(base, seen | {name}) if base else None
            if order is None:
                return None
            sequences.append(list(order))
        sequences.append([base for base in bases if base is not None])
        result = [name]
        while remaining := [sequence for sequence in sequences if sequence]:
            candidate = next(
                (
                    seq[0]
                    for seq in remaining
                    if all(seq[0] not in other[1:] for other in remaining)
                ),
                None,
            )
            if candidate is None:
                return None
            result.append(candidate)
            for sequence in remaining:
                if sequence[0] == candidate:
                    sequence.pop(0)
        return result

    def method(self, owner: str, name: str) -> SourceDefinition | None:
        # Direct methods can be resolved even when an external base is unknown.
        for base in self.mro(owner) or [owner]:
            definition = self.definitions.get(f"{base}.{name}")
            if definition is not None:
                return definition
        return None

    def exception(self, name: str) -> bool:
        return "builtins.Exception" in (self.mro(name) or [])

    def framework_exception(self, name: str) -> bool:
        return name in _FRAMEWORK_EXCEPTIONS

    def websocket_exception(self, name: str) -> bool:
        return "starlette.exceptions.WebSocketException" in (self.mro(name) or [])

    def classmethod(self, function: SourceDefinition) -> bool:
        return isinstance(
            function.node, ast.FunctionDef | ast.AsyncFunctionDef
        ) and any(
            self.resolve(decorator, function.module) == "builtins.classmethod"
            for decorator in function.node.decorator_list
        )

    def condition(
        self, node: ast.expr, module: str, env: SymbolEnvironment
    ) -> bool | None:
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            result = self.condition(node.operand, module, env)
            return not result if result is not None else None
        if (
            isinstance(node, ast.Call)
            and self.resolve(node.func, module, env) == "builtins.isinstance"
            and len(node.args) == 2
            and not node.keywords
        ):
            value = self.infer(node.args[0], module, env)
            target = self.resolve(node.args[1], module, env)
            if value.instance and value.name in _REQUESTS | _WEBSOCKETS:
                if target in _WEBSOCKETS:
                    return value.name in _WEBSOCKETS
                if target in _REQUESTS:
                    return value.name in _REQUESTS
        return None

    def infer(
        self,
        node: ast.expr,
        module: str,
        env: SymbolEnvironment,
        seen: frozenset[str] = frozenset(),
        *,
        awaited: bool = False,
    ) -> SymbolValue:
        key = self.key(node)
        if key in env:
            return env[key]
        match node:
            case ast.Await():
                return self.infer(node.value, module, env, seen, awaited=True)
            case ast.Call():
                target = self.infer(node.func, module, env, seen)
                receiver_type = target.receiver
                definition = self.definitions.get(target.name)
                if definition and isinstance(definition.node, ast.ClassDef):
                    if not target.instance:
                        return SymbolValue(definition.name, instance=True)
                    receiver_type = definition.name
                    definition = self.method(definition.name, "__call__")
                if definition and isinstance(
                    definition.node, ast.FunctionDef | ast.AsyncFunctionDef
                ):
                    if isinstance(definition.node, ast.AsyncFunctionDef) != awaited:
                        return SymbolValue()
                    if definition.node.returns:
                        name = self.annotation(
                            definition.node.returns, definition.module
                        )
                        if name in {"typing.Self", "typing_extensions.Self"}:
                            name = receiver_type
                        return SymbolValue(name or "", instance=True)
                returned = _EXTERNAL_RETURNS.get(target.name)
                if returned and returned[1] == awaited and self.external(target.name):
                    return SymbolValue(returned[0], instance=True)
                if isinstance(node.func, ast.Attribute):
                    receiver = self.infer(node.func.value, module, env, seen)
                    if receiver.name in _RESULT_TYPES and self.external(receiver.name):
                        if node.func.attr in {"unique", "yield_per"}:
                            return receiver
                        asynchronous = "Async" in receiver.name.rsplit(".", 1)[-1]
                        prefix = (
                            "sqlalchemy.ext.asyncio.Async"
                            if asynchronous
                            else "sqlalchemy.engine."
                        )
                        if node.func.attr == "scalars":
                            return SymbolValue(f"{prefix}ScalarResult", instance=True)
                        if node.func.attr == "mappings":
                            return SymbolValue(f"{prefix}MappingResult", instance=True)
                return SymbolValue()
            case ast.Name() | ast.Attribute():
                if isinstance(node, ast.Name):
                    local = f"{module}.{node.id}"
                    binding = self.bindings.get(local)
                    if isinstance(binding, tuple):
                        if local in seen:
                            return SymbolValue()
                        return self.infer(binding[1], binding[0], {}, seen | {local})
                name = self.resolve(node, module, env)
                if not name or name in seen:
                    return SymbolValue()
                binding = self.bindings.get(name)
                if isinstance(binding, tuple):
                    return self.infer(binding[1], binding[0], {}, seen | {name})
                if isinstance(node, ast.Attribute):
                    receiver = self.infer(node.value, module, env, seen)
                    field_type = self._field(receiver.name, node.attr)
                    if field_type:
                        return SymbolValue(field_type, instance=True)
                    return SymbolValue(name, receiver=receiver.name or None)
                return SymbolValue(name)
        return SymbolValue()

    def external(self, name: str) -> bool:
        """Whether *name* belongs to an importable package outside the roots.

        Locating the top-level package searches the import path without
        executing it. A package that overlaps a scanned root, such as the
        installed application of a partial scan, is not external.
        """
        package = name.partition(".")[0]
        if package not in self._externals:
            self._externals[package] = self._locate(package)
        return self._externals[package]

    def origin(
        self,
        node: ast.expr,
        module: str,
        env: SymbolEnvironment | None = None,
        seen: frozenset[str] = frozenset(),
    ) -> str | None:
        """The external callable or value that *node* evaluates or derives from."""
        name = self.resolve(node, module, env)
        if name is not None:
            if name in _DYNAMIC or name in self.definitions or name in seen:
                return None
            if self.external(name):
                return name
            binding = self.bindings.get(name)
            if isinstance(binding, tuple):
                return self.origin(binding[1], binding[0], seen=seen | {name})
            owner, _, attribute = name.rpartition(".")
            annotation = self._field(owner, attribute)
            if annotation is not None:
                return annotation if self.external(annotation) else None
        match node:
            case ast.Constant() if node.value is not None:
                return f"builtins.{type(node.value).__name__}"
            case ast.JoinedStr():
                return "builtins.str"
            case ast.List() | ast.Tuple() | ast.Set() | ast.Dict():
                return f"builtins.{type(node).__name__.lower()}"
            case ast.Await():
                return self.origin(node.value, module, env, seen)
            case ast.Call():
                return self.origin(node.func, module, env, seen)
            case ast.Subscript():
                return self.origin(node.value, module, env, seen)
            case ast.Attribute():
                receiver = self.origin(node.value, module, env, seen)
                # An untyped call result may be an application object returned
                # by the library; application method names stay unresolved.
                if receiver is None or (
                    isinstance(node.value, ast.Call | ast.Await | ast.Subscript)
                    and node.attr in self.methods
                ):
                    return None
                return f"{receiver}.{node.attr}"
        return None

    def lineage(self, name: str) -> tuple[list[str], bool] | None:
        """Scanned classes of *name*'s hierarchy and whether an external base joins.

        Returns ``None`` when a base resolves neither to a scanned class nor to
        an external package.
        """
        order = self.mro(name)
        if order is not None:
            return [item for item in order if item in self.definitions], False
        classes: list[str] = []
        external = False
        pending = [name]
        while pending:
            current = pending.pop(0)
            if current in classes:
                continue
            definition = self.definitions.get(current)
            if definition is None or not isinstance(definition.node, ast.ClassDef):
                return None
            classes.append(current)
            for base in definition.node.bases:
                if isinstance(base, ast.Subscript):
                    base = base.value
                resolved = self.resolve(base, definition.module)
                if resolved in self.definitions:
                    pending.append(resolved)
                elif resolved and self.external(resolved):
                    external = True
                else:
                    return None
        return (classes, True) if external else None

    @property
    def methods(self) -> frozenset[str]:
        """Names of all methods defined by scanned classes."""
        if self._methods is None:
            self._methods = frozenset(
                definition.node.name
                for definition in self.definitions.values()
                if definition.owner
            )
        return self._methods

    def abstract(self, function: SourceDefinition) -> bool:
        """Whether *function* is an abstract method or a Protocol member."""
        node = function.node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return False
        if any(
            self.resolve(decorator, function.module) == "abc.abstractmethod"
            for decorator in node.decorator_list
        ):
            return True
        owner = self.definitions.get(function.owner or "")
        return (
            owner is not None
            and isinstance(owner.node, ast.ClassDef)
            and any(
                self.resolve(
                    base.value if isinstance(base, ast.Subscript) else base,
                    owner.module,
                )
                in _PROTOCOLS
                for base in owner.node.bases
            )
        )

    def implementations(self, port: str) -> tuple[str | None, ...]:
        """Classes that Dishka providers in the source bind to *port*.

        ``None`` marks a binding whose implementation is not statically known.
        """
        if self._ports is None:
            previous = self.touched
            self._ports = {}
            try:
                for definition in list(self.definitions.values()):
                    if isinstance(definition.node, ast.ClassDef) and self._provider(
                        definition.name
                    ):
                        self._bind(definition.module, definition.node)
            finally:
                self.touched = previous
        bound = self._ports.get(port, [])
        self.touched.update(module for _, module in bound)
        return tuple(dict.fromkeys(implementation for implementation, _ in bound))

    def subclasses(self, owner: str) -> list[str]:
        """Classes in the source that inherit from *owner*."""
        return [
            name
            for name, definition in self.definitions.items()
            if name != owner
            and isinstance(definition.node, ast.ClassDef)
            and owner in (self.mro(name) or ())
        ]

    def _provider(self, name: str, seen: frozenset[str] = frozenset()) -> bool:
        definition = self.definitions.get(name)
        if definition is None or not isinstance(definition.node, ast.ClassDef):
            return False
        for base in definition.node.bases:
            resolved = self.resolve(base, definition.module)
            if resolved in _PROVIDERS or (
                resolved is not None
                and resolved not in seen
                and self._provider(resolved, seen | {name})
            ):
                return True
        return False

    def _bind(self, module: str, provider: ast.ClassDef) -> None:
        assert self._ports is not None
        for member in provider.body:
            provides: ast.expr | None = None
            implementations: list[str | None] = []
            if isinstance(member, ast.Assign | ast.AnnAssign) and isinstance(
                member.value, ast.Call
            ):
                call = member.value
                if self.resolve(call.func, module) not in _PROVIDE:
                    continue
                provides = _keyword(call, "provides")
                source = call.args[0] if call.args else _keyword(call, "source")
                if source is not None:
                    implementations.append(self._class(source, module))
            elif isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                decorator = next(
                    (
                        item
                        for item in member.decorator_list
                        if self.resolve(
                            item.func if isinstance(item, ast.Call) else item, module
                        )
                        in _PROVIDE
                    ),
                    None,
                )
                if decorator is None:
                    continue
                if isinstance(decorator, ast.Call):
                    provides = _keyword(decorator, "provides")
                provides = provides or self._provided(member.returns, module)
                implementations.extend(self._constructed(member, module))
            port = self.annotation(provides, module) if provides else None
            if port:
                self._ports.setdefault(port, []).extend(
                    (implementation, module) for implementation in implementations
                )

    def _provided(self, node: ast.expr | None, module: str) -> ast.expr | None:
        # Generator factories annotate Iterator[T] and similar; they provide T.
        if isinstance(node, ast.Subscript) and (
            self.resolve(node.value, module) in _ITERATORS
        ):
            if isinstance(node.slice, ast.Tuple):
                return node.slice.elts[0]
            return node.slice
        return node

    def _constructed(self, factory: _FunctionNode, module: str) -> list[str | None]:
        # Follow values at each exit, rather than resolving all names after a walk.
        pending: list[ast.AST] = list(factory.body)
        yielding = False
        while pending:
            node = pending.pop()
            if isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
            ):
                continue
            if isinstance(node, ast.Yield | ast.YieldFrom):
                yielding = True
                break
            pending.extend(ast.iter_child_nodes(node))
        arguments = factory.args
        assigned: _FactoryEnvironment = {
            arg.arg: (None,)
            for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        }
        result, remaining = self._factory_block(factory.body, module, assigned)
        if remaining is not None and not yielding:
            result.append(None)
        return result or [None]

    def _factory_block(
        self, statements: list[ast.stmt], module: str, assigned: _FactoryEnvironment
    ) -> tuple[list[str | None], _FactoryEnvironment | None]:
        assigned = dict(assigned)
        result: list[str | None] = []
        for statement in statements:
            match statement:
                case ast.Assign(targets=targets, value=value):
                    resolved = self._factory_value(value, module, assigned)
                    for target in targets:
                        if not isinstance(target, ast.Name):
                            return [*result, None], None
                        assigned[target.id] = resolved
                case ast.AnnAssign(target=ast.Name(id=name), value=value):
                    if value is not None:
                        assigned[name] = self._factory_value(value, module, assigned)
                case ast.Return(value=value):
                    result.extend(self._factory_value(value, module, assigned))
                    return result, None
                case ast.Expr(value=ast.Yield(value=value)):
                    result.extend(self._factory_value(value, module, assigned))
                case ast.If(test=test, body=body, orelse=orelse):
                    if any(isinstance(node, ast.NamedExpr) for node in ast.walk(test)):
                        return [*result, None], None
                    first, left = self._factory_block(body, module, assigned)
                    second, right = self._factory_block(orelse, module, assigned)
                    result.extend([*first, *second])
                    if left is None and right is None:
                        return result, None
                    if left is None or right is None:
                        assigned = left if left is not None else right or {}
                    else:
                        assigned = {
                            name: tuple(
                                dict.fromkeys(
                                    (
                                        *left.get(name, (None,)),
                                        *right.get(name, (None,)),
                                    )
                                )
                            )
                            for name in left.keys() | right.keys()
                        }
                case ast.Raise():
                    return result, None
                case (
                    ast.FunctionDef(name=name)
                    | ast.AsyncFunctionDef(name=name)
                    | ast.ClassDef(name=name)
                ):
                    # Their bodies execute in a separate scope.
                    assigned[name] = (None,)
                case ast.Pass():
                    continue
                case ast.Expr(value=value) if not any(
                    isinstance(node, ast.NamedExpr | ast.YieldFrom | ast.Yield)
                    for node in ast.walk(value)
                ):
                    continue
                case _:
                    # Loops, exception handlers and other unmodeled control flow
                    # cannot establish a complete provider implementation.
                    return [*result, None], None
        return result, assigned

    def _factory_value(
        self, value: ast.expr | None, module: str, assigned: _FactoryEnvironment
    ) -> tuple[str | None, ...]:
        match value:
            case ast.Name(id=name):
                return assigned.get(name, (None,))
            case ast.Await(value=inner):
                return self._factory_value(inner, module, assigned)
            case ast.Call(func=constructor):
                if isinstance(constructor, ast.Name) and constructor.id in assigned:
                    return (None,)
                return (self._class(constructor, module),)
            case ast.IfExp(body=body, orelse=orelse):
                return tuple(
                    dict.fromkeys(
                        (
                            *self._factory_value(body, module, assigned),
                            *self._factory_value(orelse, module, assigned),
                        )
                    )
                )
            case _:
                return (None,)

    def _class(self, node: ast.expr, module: str) -> str | None:
        definition = self.definitions.get(self.resolve(node, module) or "")
        if definition is not None and isinstance(definition.node, ast.ClassDef):
            return definition.name
        return None

    def environment(
        self,
        function: SourceDefinition,
        receiver: str | None = None,
        dependency_transport: str | None = None,
    ) -> SymbolEnvironment:
        assert isinstance(function.node, ast.FunctionDef | ast.AsyncFunctionDef)
        env: SymbolEnvironment = {}
        arguments = function.node.args
        for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
            env[arg.arg] = SymbolValue()
            if arg.annotation is not None:
                resolved = self.annotation(arg.annotation, function.module)
                if resolved:
                    if resolved in _CONNECTIONS and dependency_transport in {
                        "http",
                        "sse",
                        "webrtc",
                    }:
                        resolved = "starlette.requests.Request"
                    env[arg.arg] = SymbolValue(resolved, instance=True)
        if function.owner:
            owner_type = receiver if receiver is not None else function.owner
            positional = [*arguments.posonlyargs, *arguments.args]
            if positional:
                if self.classmethod(function) or function.node.name == "__new__":
                    env[positional[0].arg] = SymbolValue(owner_type)
                elif not any(
                    self.resolve(decorator, function.module) == "builtins.staticmethod"
                    for decorator in function.node.decorator_list
                ):
                    env[positional[0].arg] = SymbolValue(owner_type, instance=True)
            # Base class constructors assign inherited attributes; subclass
            # assignments override them.
            for owner in reversed(self.mro(owner_type) or [owner_type]):
                constructor = self.definitions.get(f"{owner}.__init__")
                if constructor is None or not isinstance(
                    constructor.node, ast.FunctionDef
                ):
                    continue
                constructor_env: SymbolEnvironment = {
                    "self": SymbolValue(owner_type, instance=True)
                }
                for arg in (
                    constructor.node.args.posonlyargs
                    + constructor.node.args.args
                    + constructor.node.args.kwonlyargs
                ):
                    if arg.annotation:
                        resolved = self.annotation(arg.annotation, constructor.module)
                        if resolved:
                            constructor_env[arg.arg] = SymbolValue(
                                resolved, instance=True
                            )
                for statement in constructor.node.body:
                    if isinstance(statement, ast.Assign):
                        inferred = self.infer(
                            statement.value, constructor.module, constructor_env
                        )
                        if inferred.name:
                            for target in statement.targets:
                                if isinstance(target, ast.Attribute):
                                    env[self.key(target)] = inferred
        return env

    def _field(self, owner: str, attribute: str) -> str | None:
        lineage = self.lineage(owner) if owner in self.definitions else None
        for name in lineage[0] if lineage else []:
            definition = self.definitions[name]
            assert isinstance(definition.node, ast.ClassDef)
            for statement in definition.node.body:
                if (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id == attribute
                ):
                    return self.annotation(statement.annotation, definition.module)
        return None

    def _locate(self, package: str) -> bool:
        if not package.isidentifier() or package in self.paths:
            return False
        if package in sys.builtin_module_names:
            return True
        try:
            spec = importlib.util.find_spec(package)
        except (ImportError, ValueError):
            return False
        if spec is None:
            return False
        locations = [Path(item) for item in spec.submodule_search_locations or []]
        if not locations and spec.origin and spec.has_location:
            locations.append(Path(spec.origin))
        for location in locations:
            location = location.resolve()
            for root in self.roots:
                if root.is_relative_to(location) or location.is_relative_to(root):
                    return False
        return True


def _keyword(node: ast.Call, name: str) -> ast.expr | None:
    return next((item.value for item in node.keywords if item.arg == name), None)
