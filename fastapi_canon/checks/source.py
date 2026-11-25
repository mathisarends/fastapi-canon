import ast
import builtins
import importlib.util
import sys
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Self

type _FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
type SymbolEnvironment = dict[str, SymbolValue]
type _FactoryEnvironment = dict[str, tuple[str | None, ...]]
type _RpcChannelStatus = Literal["supported", "incomplete"]

_PROPERTIES = frozenset({"builtins.property", "functools.cached_property"})
# Builtins that dispatch by computed names or code rather than protocol methods.
_DYNAMIC = frozenset(
    f"builtins.{name}"
    for name in (
        "__import__",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "setattr",
        "super",
    )
)
LIBRARY_VALUE = "builtins.object"
"""Type of a value that is known not to be an application object."""
_OPAQUE_ANNOTATIONS = frozenset(
    {
        "builtins.object",
        "builtins.type",
        "typing.Any",
        "typing.Type",
        "typing.Callable",
        "typing.Awaitable",
        "typing.Coroutine",
        "collections.abc.Callable",
        "collections.abc.Awaitable",
        "collections.abc.Coroutine",
    }
)
_TRANSPARENT_ANNOTATIONS = frozenset(
    {
        "typing.Annotated",
        "typing_extensions.Annotated",
        "typing.ClassVar",
        "typing.Final",
        "typing.Required",
        "typing.NotRequired",
        "typing.ReadOnly",
        "typing_extensions.Required",
        "typing_extensions.NotRequired",
        "typing_extensions.ReadOnly",
        "dishka.FromDishka",
        "dishka.integrations.fastapi.FromDishka",
        "fastapi_canon.Inject",
        "fastapi_canon.routing.injection.Inject",
        "fastapi_canon.integrations.websockets.Inject",
        "sqlalchemy.orm.Mapped",
        "sqlalchemy.orm.base.Mapped",
    }
)
_UNIONS = frozenset({"typing.Union", "typing.Optional"})
_ITERABLES = frozenset(
    f"{module}.{name}"
    for module, names in {
        "builtins": ("list", "set", "frozenset"),
        "collections": ("deque",),
        "collections.abc": (
            "AsyncGenerator",
            "AsyncIterable",
            "AsyncIterator",
            "Collection",
            "Generator",
            "Iterable",
            "Iterator",
            "MutableSequence",
            "MutableSet",
            "Sequence",
            "Set",
        ),
        "typing": (
            "AbstractSet",
            "AsyncGenerator",
            "AsyncIterable",
            "AsyncIterator",
            "Collection",
            "Deque",
            "FrozenSet",
            "Generator",
            "Iterable",
            "Iterator",
            "List",
            "MutableSequence",
            "MutableSet",
            "Sequence",
            "Set",
        ),
    }.items()
    for name in names
)
_MAPPINGS = frozenset(
    {
        "builtins.dict",
        "collections.OrderedDict",
        "collections.defaultdict",
        "collections.abc.Mapping",
        "collections.abc.MutableMapping",
        "typing.Dict",
        "typing.Mapping",
        "typing.MutableMapping",
    }
)
_TUPLES = frozenset({"builtins.tuple", "typing.Tuple"})
_GENERICS = frozenset({"typing.Generic", "typing_extensions.Generic"})
_CALLABLES = frozenset({"typing.Callable", "collections.abc.Callable"})
_AWAITABLES = frozenset(
    {
        "typing.Awaitable",
        "typing.Coroutine",
        "collections.abc.Awaitable",
        "collections.abc.Coroutine",
    }
)
# Builtins that build a container from the iterable they receive.
_COPIES = frozenset(
    f"builtins.{name}" for name in ("list", "tuple", "set", "frozenset", "sorted")
)
ASYNC_CONTEXT_MANAGER = "contextlib.AbstractAsyncContextManager"
CONTEXT_MANAGERS = frozenset(
    {
        ASYNC_CONTEXT_MANAGER,
        "contextlib.AbstractContextManager",
        "typing.AsyncContextManager",
        "typing.ContextManager",
    }
)
# Canon's scope factories resolve their type argument in a fresh Dishka scope.
_SERVICE_SCOPES = frozenset(
    {
        "fastapi_canon.ServiceScope",
        "fastapi_canon.composition.ServiceScope",
        "fastapi_canon.composition.scopes.ServiceScope",
    }
)
SCOPED_CALLS = frozenset(
    {
        "fastapi_canon.Scoped",
        "fastapi_canon.composition.Scoped",
        "fastapi_canon.composition.scopes.Scoped",
    }
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
_ALIASES = frozenset({"dishka.alias", "dishka.provider.alias"})
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
        "fastapi_canon.routing.api_router.APIRouter",
        "fastapi_canon.routing.http.CanonRouter",
    }
)
_FACTORY_DECLARATIONS = _ROUTER_DECLARATIONS | {
    "fastapi_canon.Error",
    "fastapi_canon.error.Error",
    "fastapi_canon.error.error.Error",
    "fastapi_canon.RouterPolicy",
    "fastapi_canon.routing.policy.RouterPolicy",
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
_RPC_ERRORS = frozenset(
    {
        "fastapi_canon.integrations.websockets.RpcError",
        "fastapi_canon.integrations.websockets.errors.RpcError",
    }
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


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    name: str
    module: str
    node: _FunctionNode | ast.ClassDef
    owner: str | None = None


@dataclass(frozen=True, slots=True)
class SymbolValue:
    name: str = ""
    instance: bool = False
    receiver: str | None = None
    # The element type when iterating the value, from its container annotation.
    element: str = ""
    # Type arguments of a parametrized annotation, such as ``Port[Service]``;
    # for a bound method, those of its receiver.
    arguments: tuple[str, ...] = ()
    # The result type of calling a ``Callable[..., R]`` value.
    returns: str = ""


@dataclass(frozen=True, slots=True)
class _RouterInclude:
    module: str
    line: int
    parent_module: str
    parent: ast.expr
    extends_contract: bool
    unconditional: bool


class SourceIndex:
    """Own source discovery, dependency tracking and memoized symbol resolution."""

    def __init__(self) -> None:
        self._paths: dict[str, Path] = {}
        self._definitions: dict[str, SourceDefinition] = {}
        self._bindings: dict[str, tuple[str, ast.expr] | str] = {}
        self._uncertain: dict[str, list[str]] = {}
        self._nested: list[SourceDefinition] = []
        self._touched: set[str] = set()
        self._unresolved_mounts: list[tuple[str, int]] = []
        self._router_parents: dict[ast.expr, list[_RouterInclude]] = {}
        self._feature_routers: set[ast.expr] = set()
        self._error_declarations: list[tuple[str, ast.expr]] = []
        self._roots: list[Path] = []
        self._references: set[str] = set()
        self._externals: dict[str, bool] = {}
        self._members: dict[str, list[str]] | None = None
        self._keys: dict[ast.expr, str] = {}
        self._mros: dict[str, tuple[tuple[str, ...] | None, frozenset[str]]] = {}
        self._trees: dict[str, ast.Module] = {}
        self._provided_values: (
            dict[str, list[tuple[SourceDefinition, str, ast.expr]] | None] | None
        ) = None
        self._constructions: _Constructions | None = None
        self._ports: dict[str, list[tuple[str | None, str]]] | None = None
        self._fields: dict[str, tuple[dict[str, SymbolValue], frozenset[str]]] = {}
        self._building: set[str] = set()
        self._local_definitions: dict[int, SourceDefinition] | None = None
        self._packages: frozenset[str] | None = None

    @property
    def paths(self) -> Mapping[str, Path]:
        return MappingProxyType(self._paths)

    @property
    def definitions(self) -> Mapping[str, SourceDefinition]:
        return MappingProxyType(self._definitions)

    @property
    def nested(self) -> tuple[SourceDefinition, ...]:
        return tuple(self._nested)

    def router_ancestors(
        self, router: ast.Call
    ) -> tuple[list[tuple[str, ast.Call]], list[tuple[str, int]]]:
        layers: list[tuple[str, ast.Call]] = []
        unresolved = list(self._unresolved_mounts)
        pending: list[tuple[ast.expr, bool]] = [(router, True)]
        visited: set[tuple[ast.expr, bool]] = set()
        while pending:
            child, certain = pending.pop()
            if (child, certain) in visited:
                continue
            visited.add((child, certain))
            parents = self._router_parents.get(child, [])
            unique = (
                certain and len(parents) == 1 and child not in self._feature_routers
            )
            for mount in parents:
                known = unique and mount.unconditional
                pending.append((mount.parent, known))
                if mount.extends_contract:
                    if known and isinstance(mount.parent, ast.Call):
                        self._touched.add(mount.parent_module)
                        layers.append((mount.parent_module, mount.parent))
                    else:
                        unresolved.append((mount.module, mount.line))
        layers.reverse()
        return layers, unresolved

    def referenced(self, module: str) -> bool:
        """Whether *module* comes from a reference root."""
        return module in self._references

    @property
    def error_declarations(self) -> tuple[tuple[str, ast.expr], ...]:
        return tuple(self._error_declarations)

    @property
    def touched(self) -> frozenset[str]:
        return frozenset(self._touched)

    def reset_usage(self, modules: set[str]) -> None:
        self._touched = set(modules)

    def record_usage(self, modules: frozenset[str]) -> None:
        self._touched.update(modules)

    def definition(self, name: str) -> SourceDefinition | None:
        found = self._definitions.get(name)
        if found is None and ".<local>." in name:
            return next((item for item in self._nested if item.name == name), None)
        return found

    def local_definition(self, node: ast.AST) -> SourceDefinition | None:
        """The indexed definition of a function nested in another function."""
        if self._local_definitions is None:
            self._local_definitions = {id(item.node): item for item in self._nested}
        return self._local_definitions.get(id(node))

    def imports(self, node: ast.Import | ast.ImportFrom, module: str) -> dict[str, str]:
        """Local names that a function-level import binds, with their targets."""
        if isinstance(node, ast.Import):
            return {
                alias.asname or alias.name.split(".")[0]: self._canonical(
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
                for alias in node.names
            }
        path = self._paths.get(module)
        package = path is not None and path.name == "__init__.py"
        prefix = module.split(".") if package else module.split(".")[:-1]
        if node.level:
            prefix = prefix[: len(prefix) - node.level + 1]
            target = ".".join([*prefix, *([node.module] if node.module else [])])
        else:
            target = node.module or ""
        return {
            alias.asname or alias.name: self._canonical(f"{target}.{alias.name}")
            for alias in node.names
            if alias.name != "*"
        }

    def iter_definitions(self) -> Iterator[SourceDefinition]:
        return iter(self._definitions.values())

    def uncertainties(self, module: str) -> tuple[str, ...]:
        return tuple(self._uncertain.get(module, ()))

    @contextmanager
    def track_usage(self) -> Iterator[None]:
        previous = self._touched
        self._touched = set()
        try:
            yield
        finally:
            previous.update(self._touched)
            self._touched = previous

    @classmethod
    def read(cls, roots: list[Path], references: Sequence[Path] = ()) -> Self:
        """Index *roots* and the definitions of *references*.

        Reference modules resolve and are followed, but declare no operations,
        catalog entries, router mounts or provider bindings.
        """
        source = cls()
        calls: list[tuple[str, ast.Call, bool | None]] = []
        for root, reference in [
            *((item, False) for item in roots),
            *((item, True) for item in references),
        ]:
            root = root.resolve()
            if not root.is_dir():
                raise ValueError(f"source root is not a directory: {root}")
            source._roots.append(root)
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
                    if module in source._paths:
                        if source._paths[module] == path and reference == (
                            module in source._references
                        ):
                            continue
                        raise ValueError(f"duplicate module {module!r}: {path}")
                    source._paths[module] = path
                    if reference:
                        source._references.add(module)
                    tree = ast.parse(path.read_text("utf-8-sig"), filename=str(path))
                    source._trees[module] = tree
                    names: set[str] = set()
                    source._index(module, tree.body, package=package, names=names)
                    indexed = {id(source._definitions[name].node) for name in names}
                    unconditional = {
                        stmt.value for stmt in tree.body if isinstance(stmt, ast.Expr)
                    }
                    for node, module_scope in _walk_scopes(tree):
                        if reference and isinstance(node, ast.Call):
                            continue
                        if isinstance(node, ast.Call):
                            source._error_declarations.extend(
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
                                item.arg in {"router_factory", "routers"}
                                for item in node.keywords
                            )
                        ):
                            direct = node in unconditional if module_scope else None
                            calls.append((module, node, direct))
                        elif (
                            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                            and id(node) not in indexed
                        ):
                            source._nested.append(
                                SourceDefinition(
                                    f"{module}.<local>.{node.name}:{node.lineno}",
                                    module,
                                    node,
                                )
                            )
        for module, call, direct in calls:
            source._index_mount(call, module, unconditional=direct)
        source._index_route_factories()
        return source

    def _index_mount(
        self, call: ast.Call, module: str, *, unconditional: bool | None
    ) -> None:
        if isinstance(call.func, ast.Attribute) and call.func.attr == "include_router":
            scope, parent = self.value(call.func.value, module)
            extends = self._extends_included_routes(parent, scope, set())
            child = call.args[0] if call.args else _keyword(call, "router")
            # Local parameters or assignments can shadow a module-level router.
            if child is not None and unconditional is not None:
                child_scope, declaration = self.value(child, module)
                if (
                    isinstance(declaration, ast.Call)
                    and self.resolve(declaration.func, child_scope)
                    in _ROUTER_DECLARATIONS
                ):
                    known = (
                        unconditional
                        and isinstance(parent, ast.Call)
                        and self.resolve(parent.func, scope) in _ROUTER_DECLARATIONS
                    )
                    self._router_parents.setdefault(declaration, []).append(
                        _RouterInclude(
                            module, call.lineno, scope, parent, extends, known
                        )
                    )
                    return
            if extends:
                self._unresolved_mounts.append((module, call.lineno))
        else:
            target = self.resolve(call.func, module)
            if target in {
                "fastapi_canon.Composition",
                "fastapi_canon.composition.application.Composition",
            }:
                factory = _keyword(call, "router_factory")
                if factory is not None and self._extends_included_routes(
                    factory, module, set()
                ):
                    self._unresolved_mounts.append((module, call.lineno))
            elif target in {
                "fastapi_canon.Feature",
                "fastapi_canon.composition.feature.Feature",
            }:
                routers = _keyword(call, "routers")
                if routers is not None:
                    scope, sequence = self.value(routers, module)
                    if isinstance(sequence, ast.Tuple | ast.List):
                        self._feature_routers.update(
                            self.value(item, scope)[1] for item in sequence.elts
                        )

    def _extends_included_routes(
        self, node: ast.expr, module: str, seen: set[int]
    ) -> bool:
        # These Canon router options also affect included operations.
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
                    item.arg in {"raises", "http_statuses", "policy", "dependencies"}
                    and not (
                        isinstance(item.value, ast.Constant)
                        and item.value.value is None
                    )
                )
                for item in keywords
            )
        definition = self._definitions.get(name) if name is not None else None
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
        for factory in list(self._definitions.values()):
            node = factory.node
            if (
                factory.owner
                or factory.module in self._references
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
            self._paths[scope] = self._paths[factory.module]
            for name in self._bindings.keys() | self._definitions.keys():
                parent, _, local = name.rpartition(".")
                if parent == factory.module and local not in names:
                    self._bindings[f"{scope}.{local}"] = name
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
                    and self.rpc_channel_contract(value, module) is None
                ):
                    valid_values = False
            if not valid_values:
                self._paths.pop(scope)
                self._bindings = {
                    name: value
                    for name, value in self._bindings.items()
                    if not name.startswith(f"{scope}.")
                }
                self._definitions = {
                    name: definition
                    for name, definition in self._definitions.items()
                    if not name.startswith(f"{scope}.")
                }
                self._uncertain.pop(scope, None)
                continue
            self._uncertain.setdefault(scope, []).extend(
                self._uncertain.get(factory.module, [])
            )
            indexed_nodes = {id(self._definitions[name].node) for name in indexed}
            self._nested = [
                definition
                for definition in self._nested
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
            if resolved in _FACTORY_DECLARATIONS or resolved in self._definitions:
                return True
            scope, value = self.value(node, module)
            return (value is not node or scope != module) and self._route_factory_value(
                value, scope, seen
            )
        if isinstance(node, ast.Call):
            constructor = self.resolve(node.func, module)
            declaration = (
                constructor in _FACTORY_DECLARATIONS
                or self.rpc_channel_contract(node, module) is not None
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
                        self._bindings[f"{module}.{local}"] = (
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
                        self._bindings[f"{module}.{alias.asname or alias.name}"] = (
                            f"{target}.{alias.name}"
                        )
                case ast.Assign():
                    for target_node in node.targets:
                        if isinstance(target_node, ast.Name):
                            if f"{module}.{target_node.id}" in self._bindings:
                                self._uncertain.setdefault(module, []).append(
                                    f"reassigned module binding: {target_node.id}"
                                )
                            self._bindings[f"{module}.{target_node.id}"] = (
                                module,
                                node.value,
                            )
                        else:
                            self._uncertain.setdefault(module, []).append(
                                "unsupported module assignment"
                            )
                case ast.AnnAssign() if isinstance(node.target, ast.Name):
                    if node.value is not None:
                        if f"{module}.{node.target.id}" in self._bindings:
                            self._uncertain.setdefault(module, []).append(
                                f"reassigned module binding: {node.target.id}"
                            )
                        self._bindings[f"{module}.{node.target.id}"] = (
                            module,
                            node.value,
                        )
                case ast.TypeAlias() if isinstance(node.name, ast.Name):
                    self._bindings[f"{module}.{node.name.id}"] = module, node.value
                case (
                    ast.AugAssign()
                    | ast.Delete()
                    | ast.For()
                    | ast.While()
                    | ast.Try()
                    | ast.With()
                    | ast.Match()
                ):
                    self._uncertain.setdefault(module, []).append(
                        "dynamic module declarations"
                    )
                case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
                    name = f"{module}.{node.name}"
                    self._definitions[name] = SourceDefinition(name, module, node)
                    names.add(name)
                    if isinstance(node, ast.ClassDef):
                        for member in node.body:
                            if isinstance(
                                member, ast.FunctionDef | ast.AsyncFunctionDef
                            ):
                                key = f"{name}.{member.name}"
                                self._definitions[key] = SourceDefinition(
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
                        self._uncertain.setdefault(module, []).append(
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
                    self._uncertain.setdefault(module, []).append(
                        "dynamic module declarations or contract mutation"
                    )

    def _canonical(self, name: str, seen: frozenset[str] = frozenset()) -> str:
        module = name.rpartition(".")[0]
        if module in self._paths:
            self._touched.add(module)
        if name in seen:
            return name
        binding = self._bindings.get(name)
        if isinstance(binding, str):
            return self._canonical(binding, seen | {name})
        if binding is not None and isinstance(binding[1], ast.Name | ast.Attribute):
            return self.resolve(binding[1], binding[0], seen=seen | {name}) or name
        # Package attributes can share a name with a source module. Resolve
        # aliases inside a namespace without rewriting an actual module prefix.
        prefix, separator, member = name.rpartition(".")
        if (
            separator
            and prefix not in self._paths
            and prefix in self._bindings
            and prefix not in seen
        ):
            resolved = self._canonical(prefix, seen | {name})
            if resolved != prefix:
                return self._canonical(f"{resolved}.{member}", seen | {name})
        return _FRAMEWORK_ALIASES.get(name, name)

    def rpc_channel_contract(
        self, node: ast.expr, module: str
    ) -> tuple[_RpcChannelStatus, list[tuple[str, ast.Call]]] | None:
        """Resolve a channel declaration and its child calls in inheritance order."""
        scope, declaration = self.value(node, module)
        layers: list[tuple[str, ast.Call]] = []
        visited: set[ast.Call] = set()
        while isinstance(declaration, ast.Call) and declaration not in visited:
            visited.add(declaration)
            layers.append((scope, declaration))
            constructor = declaration.func
            status = self._rpc_channel(self.resolve(constructor, scope))
            if status is not None:
                layers.reverse()
                return status, layers
            if (
                not isinstance(constructor, ast.Attribute)
                or constructor.attr != "child"
            ):
                break
            scope, declaration = self.value(constructor.value, scope)
        return None

    def _rpc_channel(self, name: str | None) -> _RpcChannelStatus | None:
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
            definition = self._definitions.get(current)
            if definition is None or not isinstance(definition.node, ast.ClassDef):
                supported = False
                continue
            self._touched.add(definition.module)
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

    def exception(self, name: str) -> bool:
        """Whether *name* is an exception class.

        A scanned class with a library base is assumed to be an exception: the
        library hierarchy is not imported, and such a class is only checked
        where it is raised, caught or mapped.
        """
        order = self.mro(name) or ()
        return "builtins.Exception" in order or (
            name in self._definitions and any(self._opaque(item) for item in order)
        )

    def origin(
        self,
        node: ast.expr,
        module: str,
        env: SymbolEnvironment | None = None,
        *,
        seen: frozenset[str] = frozenset(),
    ) -> str | None:
        """The external callable or value that *node* evaluates or derives from."""
        name = self.resolve(node, module, env)
        if name is not None:
            if name in _DYNAMIC or name in self._definitions or name in seen:
                return None
            if self.external(name):
                return name
            binding = self._bindings.get(name)
            if isinstance(binding, tuple):
                return self.origin(binding[1], binding[0], seen=seen | {name})
            owner, _, attribute = name.rpartition(".")
            field = (
                self.fields(owner).get(attribute)
                if owner in self._definitions
                else None
            )
            if field is not None:
                return (
                    field.name if field.instance and self.library(field.name) else None
                )
        match node:
            case ast.Constant() if node.value is not None:
                return f"builtins.{type(node.value).__name__}"
            case ast.JoinedStr():
                return "builtins.str"
            case ast.List() | ast.Tuple() | ast.Set() | ast.Dict():
                return f"builtins.{type(node).__name__.lower()}"
            case ast.ListComp() | ast.SetComp() | ast.DictComp():
                return f"builtins.{type(node).__name__[:-4].lower()}"
            case ast.Await():
                return self.origin(node.value, module, env, seen=seen)
            case ast.Call():
                return self.origin(node.func, module, env, seen=seen)
            case ast.Subscript():
                return self.origin(node.value, module, env, seen=seen)
            case ast.Attribute():
                receiver = self.origin(node.value, module, env, seen=seen)
                # An untyped call result may be an application object returned
                # by the library; application method names stay unresolved.
                if receiver is None or (
                    isinstance(node.value, ast.Call | ast.Await | ast.Subscript)
                    and node.attr in self.members
                ):
                    return None
                return f"{receiver}.{node.attr}"
        return None

    def library(self, name: str) -> bool:
        """Whether *name* lies outside the scanned roots in a locatable package."""
        return name not in self._definitions and self.external(name)

    def unlocated(self, name: str) -> bool:
        """Whether *name* comes from a package that is neither scanned nor locatable."""
        if self._packages is None:
            self._packages = frozenset(
                module.partition(".")[0] for module in self._paths
            )
        return name.partition(".")[0] not in self._packages and not self.external(name)

    def framework_exception(self, name: str) -> bool:
        return name in _FRAMEWORK_EXCEPTIONS

    def websocket_exception(self, name: str) -> bool:
        return "starlette.exceptions.WebSocketException" in (self.mro(name) or [])

    def rpc_error(self, name: str) -> bool:
        """Whether *name* is an application RpcError, which is its own contract."""
        return name in self._definitions and not _RPC_ERRORS.isdisjoint(
            self.mro(name) or ()
        )

    def _opaque(self, name: str) -> bool:
        # A library class whose own bases are not read.
        return (
            name not in self._definitions
            and not name.startswith("builtins.")
            and name not in _MARKER_BASES
            and name not in _FRAMEWORK_EXCEPTIONS
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

    def _mro(self, name: str, seen: frozenset[str]) -> list[str] | None:
        if name in _FRAMEWORK_EXCEPTIONS:
            framework_base = _FRAMEWORK_EXCEPTIONS[name]
            order = self.mro(framework_base, seen | {name})
            return [name, *order] if order else None
        if name.startswith("builtins.") and name.count(".") == 1:
            value = getattr(builtins, name.split(".")[1], None)
            if isinstance(value, type):
                return [f"builtins.{base.__name__}" for base in value.__mro__]
        if name in _MARKER_BASES:
            return [name, "builtins.object"]
        definition = self._definitions.get(name)
        if definition is None or not isinstance(definition.node, ast.ClassDef):
            # Library bases are opaque: their own ancestors are not read.
            return [name, "builtins.object"] if self.library(name) else None
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
        direct_bases = [base for base in bases if base is not None]
        sequences.append(direct_bases)
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
                value = self.typed(arg.annotation, function.module)
                if value.name in _CONNECTIONS and dependency_transport in {
                    "http",
                    "sse",
                    "webrtc",
                }:
                    value = SymbolValue("starlette.requests.Request", instance=True)
                if value.name or value.returns:
                    env[arg.arg] = value
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
                    receiver_name = positional[0].arg
                    env[receiver_name] = SymbolValue(owner_type, instance=True)
                    for attribute, value in self.fields(owner_type).items():
                        env[f"{receiver_name}.{attribute}"] = value
        return env

    def fields(self, owner: str) -> Mapping[str, SymbolValue]:
        """Known attribute types of instances of the scanned class *owner*.

        Class-body annotations and assignments come first; assignments in
        ``__init__`` override them, and subclasses override their bases.
        """
        if owner in self._fields:
            cached, modules = self._fields[owner]
            self._touched.update(modules)
            return cached
        definition = self._definitions.get(owner)
        if owner in self._building or definition is None:
            return {}
        if not isinstance(definition.node, ast.ClassDef):
            # Attributes of functions and properties are not instance fields.
            return {}
        self._building.add(owner)
        previous = self._touched
        self._touched = set()
        result: dict[str, SymbolValue] = {}
        try:
            lineage = self.lineage(owner)
            for name in reversed(lineage[0] if lineage else [owner]):
                definition = self._definitions[name]
                if not isinstance(definition.node, ast.ClassDef):
                    continue
                for statement in definition.node.body:
                    self._field(
                        statement, definition.module, {}, receiver=None, result=result
                    )
                constructor = self._definitions.get(f"{name}.__init__")
                if constructor is None or not isinstance(
                    constructor.node, ast.FunctionDef
                ):
                    continue
                parameters = [
                    *constructor.node.args.posonlyargs,
                    *constructor.node.args.args,
                    *constructor.node.args.kwonlyargs,
                ]
                if not parameters:
                    continue
                receiver = parameters[0].arg
                constructor_env: SymbolEnvironment = {
                    receiver: SymbolValue(owner, instance=True)
                }
                for arg in parameters[1:]:
                    if arg.annotation:
                        value = self.typed(arg.annotation, constructor.module)
                        if value.name or value.returns:
                            constructor_env[arg.arg] = value
                assigned: dict[str, SymbolValue] = {}
                conflicting: set[str] = set()
                for statement in _nested_statements(constructor.node.body):
                    single: dict[str, SymbolValue] = {}
                    self._field(
                        statement,
                        constructor.module,
                        constructor_env,
                        receiver=receiver,
                        result=single,
                    )
                    for attribute, value in single.items():
                        if assigned.get(attribute, value) != value:
                            conflicting.add(attribute)
                        assigned[attribute] = value
                for attribute in conflicting:
                    # Branches assign different types: keep a declared class
                    # annotation, otherwise the type is unknown.
                    assigned[attribute] = result.get(attribute, SymbolValue())
                result.update(assigned)
        finally:
            self._building.discard(owner)
            modules = frozenset(self._touched)
            previous.update(self._touched)
            self._touched = previous
        self._fields[owner] = result, modules
        return result

    def attribute(self, owner: str, name: str) -> bool:
        """Whether instances of *owner* may hold a callable in attribute *name*."""
        lineage = self.lineage(owner)
        classes = [*(lineage[0] if lineage else [owner]), *self.subclasses(owner)]
        return any(
            name in self._callable_attributes(self._definitions[item])
            for item in dict.fromkeys(classes)
            if isinstance(self._definitions[item].node, ast.ClassDef)
        )

    def attribute_sources(
        self, owner: str, name: str
    ) -> list[tuple[SourceDefinition | None, str, ast.expr]] | None:
        """Every expression that constructing *owner* stores in attribute *name*.

        Each entry is the enclosing function (``None`` at module level), its
        module and the argument or default. ``None`` when another assignment,
        an expanded argument or a class used as a value hides a source.
        """
        lineage = self.lineage(owner)
        if lineage is None or lineage[1]:
            # A library base can construct the class with arguments of its own.
            return None
        classes = lineage[0]
        constructor = next(
            (
                self._definitions[f"{item}.__init__"]
                for item in classes
                if f"{item}.__init__" in self._definitions
            ),
            None,
        )
        parameter = self._stored_parameter(classes, constructor, name)
        if parameter is None or (
            constructor is None
            and not self._construction_index().replaced.isdisjoint({name, "**"})
        ):
            return None
        argument, position, default, module = parameter
        return self._argument_sources(
            owner,
            constructor,
            argument=argument,
            position=position,
            default=(default, module),
        )

    def parameter_sources(
        self, constructor: SourceDefinition, name: str
    ) -> list[tuple[SourceDefinition | None, str, ast.expr]] | None:
        """Every expression that constructions pass to parameter *name* of an ``__init__``."""
        node = constructor.node
        if (
            constructor.owner is None
            or not isinstance(node, ast.FunctionDef)
            or node.name != "__init__"
        ):
            return None
        lineage = self.lineage(constructor.owner)
        if lineage is None or lineage[1]:
            return None
        arguments = node.args
        positional = [
            item.arg for item in [*arguments.posonlyargs, *arguments.args][1:]
        ]
        defaults = _defaults(arguments)
        if name not in positional and name not in {
            item.arg for item in arguments.kwonlyargs
        }:
            return None
        return self._argument_sources(
            constructor.owner,
            constructor,
            argument=name,
            position=positional.index(name) if name in positional else None,
            default=(defaults.get(name), constructor.module),
        )

    def lineage(self, name: str) -> tuple[list[str], bool] | None:
        """Scanned classes of *name*'s hierarchy and whether an external base joins.

        Returns ``None`` when a base resolves neither to a scanned class nor to
        an external package.
        """
        order = self.mro(name)
        if order is None:
            return None
        return [item for item in order if item in self._definitions], any(
            self._opaque(item) for item in order
        )

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
        owner = self._definitions.get(function.owner or "")
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
            previous = self._touched
            self._ports = {}
            try:
                for definition in list(self._definitions.values()):
                    if (
                        isinstance(definition.node, ast.ClassDef)
                        and definition.module not in self._references
                        and self._provider(definition.name)
                    ):
                        self._bind(definition.module, definition.node)
            finally:
                self._touched = previous
        bound = self._ports.get(port, [])
        self._touched.update(module for _, module in bound)
        return tuple(dict.fromkeys(implementation for implementation, _ in bound))

    def injected_argument(
        self, constructor: SourceDefinition | None, argument: str
    ) -> list[tuple[SourceDefinition, str, ast.expr]] | None:
        """What provider methods return for a parameter that Dishka fills."""
        if constructor is None or not isinstance(
            constructor.node, ast.FunctionDef | ast.AsyncFunctionDef
        ):
            return None
        parameter = next(
            (
                item
                for item in [
                    *constructor.node.args.posonlyargs,
                    *constructor.node.args.args,
                    *constructor.node.args.kwonlyargs,
                ]
                if item.arg == argument
            ),
            None,
        )
        if parameter is None or parameter.annotation is None:
            return None
        port = self._port_key(parameter.annotation, constructor.module)
        return self.provided(port) if port else None

    def provided(
        self, port: str
    ) -> list[tuple[SourceDefinition, str, ast.expr]] | None:
        """Values that Dishka provider methods in the roots return for *port*.

        *port* is the resolved name of the provided annotation, which may be a
        type alias. ``None`` when no provider method or another binding provides it.
        """
        if self._provided_values is None:
            previous = self._touched
            self._touched = set()
            self._provided_values = {}
            try:
                for definition in list(self._definitions.values()):
                    if (
                        isinstance(definition.node, ast.ClassDef)
                        and definition.module not in self._references
                        and self._provider(definition.name)
                    ):
                        self._index_provided(definition)
            finally:
                self._touched = previous
        values = self._provided_values.get(port)
        if values is not None:
            self._touched.update(module for _, module, _ in values)
        return values

    def provider_method(self, definition: SourceDefinition) -> bool:
        """Whether Dishka calls *definition* and resolves its parameters."""
        return (
            definition.owner is not None
            and self._provider(definition.owner)
            and isinstance(definition.node, ast.FunctionDef | ast.AsyncFunctionDef)
            and any(
                self.resolve(
                    item.func if isinstance(item, ast.Call) else item,
                    definition.module,
                )
                in _PROVIDE
                for item in definition.node.decorator_list
            )
        )

    def _index_provided(self, provider: SourceDefinition) -> None:
        assert self._provided_values is not None
        assert isinstance(provider.node, ast.ClassDef)
        for member in provider.node.body:
            port: str | None = None
            values: list[tuple[SourceDefinition, str, ast.expr]] | None = None
            definition = self._definitions.get(
                f"{provider.name}.{getattr(member, 'name', '')}"
            )
            if isinstance(member, ast.Assign | ast.AnnAssign) and isinstance(
                member.value, ast.Call
            ):
                call = member.value
                if self.resolve(call.func, provider.module) not in _PROVIDE:
                    continue
                provides = _keyword(call, "provides")
                port = self._port_key(provides, provider.module) if provides else None
            elif definition is not None and self.provider_method(definition):
                assert isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                decorator = next(
                    (
                        item
                        for item in member.decorator_list
                        if isinstance(item, ast.Call)
                        and self.resolve(item.func, provider.module) in _PROVIDE
                    ),
                    None,
                )
                provides = _keyword(decorator, "provides") if decorator else None
                provides = provides or self._provided(member.returns, provider.module)
                port = self._port_key(provides, provider.module) if provides else None
                values = [
                    (definition, provider.module, node.value)
                    for node in _executed(member.body)
                    if isinstance(node, ast.Return | ast.Yield)
                    and node.value is not None
                ]
            if port is None:
                continue
            known = self._provided_values.get(port, [])
            self._provided_values[port] = (
                None if values is None or known is None else [*known, *values]
            )

    def _port_key(self, node: ast.expr, module: str) -> str | None:
        # The resolved name a provider annotation or an injected parameter names.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return None
        while isinstance(node, ast.Subscript) and (
            self.resolve(node.value, module) in _TRANSPARENT_ANNOTATIONS
        ):
            node = (
                node.slice.elts[0] if isinstance(node.slice, ast.Tuple) else node.slice
            )
        if isinstance(node, ast.Subscript):
            node = node.value
        return self.resolve(node, module)

    def implementers(self, port: str) -> list[str]:
        """Scanned classes that can stand in for *port*.

        These are its subclasses and, for a ``Protocol``, classes that define
        every method the protocol declares.
        """
        result = self.subclasses(port)
        definition = self._definitions.get(port)
        if definition is None or not isinstance(definition.node, ast.ClassDef):
            return result
        if not any(
            self.resolve(
                base.value if isinstance(base, ast.Subscript) else base,
                definition.module,
            )
            in _PROTOCOLS
            for base in definition.node.bases
        ):
            return result
        required = [
            member.name
            for member in definition.node.body
            if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for name, candidate in self._definitions.items():
            if (
                name == port
                or name in result
                or not isinstance(candidate.node, ast.ClassDef)
                or not required
            ):
                continue
            methods = [self.method(name, item) for item in required]
            if all(
                method is not None and not self.abstract(method) for method in methods
            ):
                result.append(name)
        return result

    def subclasses(self, owner: str) -> list[str]:
        """Classes in the source that inherit from *owner*."""
        return [
            name
            for name, definition in self._definitions.items()
            if name != owner
            and isinstance(definition.node, ast.ClassDef)
            and owner in (self.mro(name) or ())
        ]

    def http_connection(
        self, definition: SourceDefinition, node: ast.Call, argument: ast.expr
    ) -> bool:
        """Whether *argument* fills a connection parameter of *definition*."""
        parameter = self.parameters(definition, node).get(id(argument))
        return (
            parameter is not None
            and parameter.annotation is not None
            and self.annotation(parameter.annotation, definition.module) in _CONNECTIONS
        )

    def parameters(
        self, definition: SourceDefinition, node: ast.Call
    ) -> dict[int, ast.arg]:
        """The parameters of *definition* that the arguments of *node* fill.

        Keys are the ``id`` of each argument expression.
        """
        callee = definition.node
        if not isinstance(callee, ast.FunctionDef | ast.AsyncFunctionDef):
            return {}
        positional = [*callee.args.posonlyargs, *callee.args.args]
        if definition.owner and positional and isinstance(node.func, ast.Attribute):
            positional = positional[1:]
        parameters = {
            id(value): parameter
            for parameter, value in zip(positional, node.args, strict=False)
        }
        named = {item.arg: item for item in [*positional, *callee.args.kwonlyargs]}
        for keyword in node.keywords:
            if keyword.arg in named:
                parameters[id(keyword.value)] = named[keyword.arg]
        return parameters

    def literal_getattr(
        self, node: ast.Call, module: str, env: SymbolEnvironment
    ) -> ast.Attribute | None:
        """``getattr(obj, "name"[, default])`` as the attribute read ``obj.name``."""
        if (
            self.resolve(node.func, module, env) != "builtins.getattr"
            or node.keywords
            or len(node.args) not in {2, 3}
        ):
            return None
        owner, name = node.args[:2]
        if not (
            isinstance(name, ast.Constant)
            and isinstance(name.value, str)
            and name.value.isidentifier()
        ):
            return None
        return ast.copy_location(
            ast.Attribute(value=owner, attr=name.value, ctx=ast.Load()), node
        )

    def unpack(
        self,
        target: ast.Tuple | ast.List,
        value: ast.expr,
        module: str,
        *,
        env: SymbolEnvironment,
    ) -> list[SymbolValue] | None:
        """The values that unpacking *value* binds to *target*'s elements, if known."""
        if any(isinstance(item, ast.Starred) for item in target.elts):
            return None
        if isinstance(value, ast.Tuple | ast.List):
            if len(value.elts) != len(target.elts) or any(
                isinstance(item, ast.Starred) for item in value.elts
            ):
                return None
            return [self.infer(item, module, env) for item in value.elts]
        inferred = self.infer(value, module, env)
        if (
            inferred.name in _TUPLES
            and len(inferred.arguments) == len(target.elts)
            and "..." not in inferred.arguments
        ):
            return [
                SymbolValue(item, instance=True) if item else SymbolValue()
                for item in inferred.arguments
            ]
        if inferred.element and inferred.name in _ITERABLES | _TUPLES:
            return [SymbolValue(inferred.element, instance=True)] * len(target.elts)
        return None

    def _argument_sources(
        self,
        owner: str,
        constructor: SourceDefinition | None,
        *,
        argument: str,
        position: int | None,
        default: tuple[ast.expr | None, str],
    ) -> list[tuple[SourceDefinition | None, str, ast.expr]] | None:
        constructed = [owner]
        for subclass in self.subclasses(owner):
            sub_lineage = self.lineage(subclass)
            sub_constructor = next(
                (
                    self._definitions[f"{item}.__init__"]
                    for item in (sub_lineage[0] if sub_lineage else [])
                    if f"{item}.__init__" in self._definitions
                ),
                None,
            )
            if sub_constructor != constructor or constructor is None:
                return None
            constructed.append(subclass)
        sources: list[tuple[SourceDefinition | None, str, ast.expr]] = []
        for item in constructed:
            calls = self._constructions_of(item)
            if calls is None:
                return None
            if item in self._construction_index().injected:
                provided = self.injected_argument(constructor, argument)
                if provided is None:
                    return None
                sources.extend(provided)
            for site, site_module, call in calls:
                if any(isinstance(value, ast.Starred) for value in call.args) or any(
                    keyword.arg is None for keyword in call.keywords
                ):
                    return None
                value = next(
                    (
                        keyword.value
                        for keyword in call.keywords
                        if keyword.arg == argument
                    ),
                    call.args[position]
                    if position is not None and position < len(call.args)
                    else None,
                )
                if value is not None:
                    sources.append((site, site_module, value))
                elif default[0] is not None:
                    sources.append((None, default[1], default[0]))
        return sources

    def _constructions_of(
        self, owner: str
    ) -> list[tuple[SourceDefinition | None, str, ast.Call]] | None:
        # Calls in the roots that construct *owner*; None when the class is
        # also handed to other code, which may construct it.
        index = self._construction_index()
        if owner in index.escaped:
            return None
        return index.calls.get(owner, [])

    def _construction_index(self) -> "_Constructions":
        if self._constructions is None:
            # Indexing reads every module; only the sources found count as used.
            previous = self._touched
            self._touched = set()
            try:
                self._constructions = self._index_constructions()
            finally:
                self._touched = previous
        return self._constructions

    def _index_constructions(self) -> "_Constructions":
        index = _Constructions({}, set())
        scopes: list[tuple[SourceDefinition | None, str, list[ast.stmt]]] = [
            (None, module, tree.body) for module, tree in self._trees.items()
        ]
        scopes.extend(
            (definition, definition.module, definition.node.body)
            for definition in [*self._definitions.values(), *self._nested]
            if isinstance(definition.node, ast.FunctionDef | ast.AsyncFunctionDef)
        )
        for site, module, body in scopes:
            nodes = [node for statement in body for node in _scope_nodes(statement)]
            receiver = self._own_class(site)
            allowed: set[int] = set()
            for node in nodes:
                allowed.update(id(item) for item in _reference_positions(node))
                injected = self._injected_class(node, module)
                if injected is not None:
                    # Dishka builds the class from its constructor annotations.
                    index.injected.add(injected[0])
                    allowed.update(id(item) for item in injected[1])
            for node in nodes:
                if isinstance(node, ast.Call):
                    if receiver is not None and _constructs_receiver(node, site):
                        # cls(...) or type(self)(...) may build any subclass.
                        index.escaped.update([receiver, *self.subclasses(receiver)])
                    name = self.resolve(node.func, module)
                    if name in {"dataclasses.replace", "copy.replace"}:
                        index.replaced.update(
                            keyword.arg or "**" for keyword in node.keywords
                        )
                    definition = self._definitions.get(name or "")
                    if definition is not None and isinstance(
                        definition.node, ast.ClassDef
                    ):
                        index.calls.setdefault(definition.name, []).append(
                            (site, module, node)
                        )
                elif (
                    isinstance(node, ast.Name | ast.Attribute)
                    and isinstance(node.ctx, ast.Load)
                    and id(node) not in allowed
                ):
                    name = self.resolve(node, module)
                    definition = self._definitions.get(name or "")
                    if definition is not None and isinstance(
                        definition.node, ast.ClassDef
                    ):
                        index.escaped.add(definition.name)
        return index

    def _injected_class(
        self, node: ast.AST, module: str
    ) -> tuple[str, list[ast.expr]] | None:
        # The class of ``provide(Class, provides=Port)`` and the references
        # that only name it or its port.
        if not isinstance(node, ast.Call) or (
            self.resolve(node.func, module) not in _PROVIDE - _ALIASES
        ):
            return None
        source = _keyword(node, "source") or (node.args[0] if node.args else None)
        if source is None or not isinstance(source, ast.Name | ast.Attribute):
            return None
        definition = self._definitions.get(self.resolve(source, module) or "")
        if definition is None or not isinstance(definition.node, ast.ClassDef):
            return None
        provides = _keyword(node, "provides")
        return definition.name, [source, *([provides] if provides else [])]

    def _own_class(self, site: SourceDefinition | None) -> str | None:
        if site is None or site.owner is None:
            return None
        owner = self._definitions.get(site.owner)
        return (
            site.owner
            if owner is not None and isinstance(owner.node, ast.ClassDef)
            else None
        )

    def _stored_parameter(
        self,
        classes: list[str],
        constructor: SourceDefinition | None,
        name: str,
    ) -> tuple[str, int | None, ast.expr | None, str] | None:
        # The constructor parameter, its position, default and module that
        # an attribute holds; only the constructor may assign the attribute.
        stores: list[tuple[SourceDefinition, ast.stmt]] = []
        for item in classes:
            definition = self._definitions[item]
            assert isinstance(definition.node, ast.ClassDef)
            for member in definition.node.body:
                method = self._definitions.get(f"{item}.{getattr(member, 'name', '')}")
                if method is None or not isinstance(
                    member, ast.FunctionDef | ast.AsyncFunctionDef
                ):
                    continue
                receiver = [*member.args.posonlyargs, *member.args.args][:1]
                stores.extend(
                    (method, statement)
                    for statement in ast.walk(member)
                    if isinstance(statement, ast.Assign | ast.AnnAssign | ast.AugAssign)
                    and receiver
                    and name in _assigned_names(statement, receiver[0].arg)
                )
        if constructor is not None:
            assert isinstance(constructor.node, ast.FunctionDef)
            arguments = constructor.node.args
            positional = [*arguments.posonlyargs, *arguments.args][1:]
            if (
                len(stores) != 1
                or stores[0][0] != constructor
                or not isinstance(stores[0][1], ast.Assign | ast.AnnAssign)
            ):
                return None
            value = stores[0][1].value
            parameters = {
                item.arg: item for item in [*positional, *arguments.kwonlyargs]
            }
            if not isinstance(value, ast.Name) or value.id not in parameters:
                return None
            defaults = _defaults(arguments)
            index = (
                [item.arg for item in positional].index(value.id)
                if any(item.arg == value.id for item in positional)
                else None
            )
            return value.id, index, defaults.get(value.id), constructor.module
        if stores:
            return None
        # A dataclass or model field: its keyword and, for a dataclass, its
        # position among the annotated fields of the hierarchy.
        fields: list[tuple[str, ast.AnnAssign, str]] = []
        dataclass = False
        for item in reversed(classes):
            definition = self._definitions[item]
            assert isinstance(definition.node, ast.ClassDef)
            dataclass = dataclass or any(
                self.resolve(
                    decorator.func if isinstance(decorator, ast.Call) else decorator,
                    definition.module,
                )
                == "dataclasses.dataclass"
                for decorator in definition.node.decorator_list
            )
            fields.extend(
                (statement.target.id, statement, definition.module)
                for statement in definition.node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and self.annotation(statement.annotation, definition.module)
                != "typing.ClassVar"
            )
        names = [field[0] for field in fields]
        if name not in names:
            return None
        _, statement, module = fields[names.index(name)]
        default = statement.value
        if isinstance(default, ast.Call):
            # field(default_factory=...) and similar specifiers are not read.
            return None
        return name, names.index(name) if dataclass else None, default, module

    def http_request(self, value: SymbolValue) -> bool:
        return value.instance and value.name in _REQUESTS

    def resolve(
        self,
        node: ast.expr,
        module: str,
        env: SymbolEnvironment | None = None,
        *,
        seen: frozenset[str] = frozenset(),
    ) -> str | None:
        self._touched.add(module)
        env = env or {}
        if env:
            text = self.key(node)
            if text in env:
                return env[text].name or None
        if isinstance(node, ast.Name):
            local = f"{module}.{node.id}"
            if local in self._bindings or local in self._definitions:
                return self._canonical(local, seen)
            if hasattr(builtins, node.id):
                return f"builtins.{node.id}"
        elif isinstance(node, ast.Attribute):
            parent = self.resolve(node.value, module, env, seen=seen)
            if parent is None and isinstance(node.value, ast.Call | ast.Await):
                parent = self.infer(node.value, module, env).name
            getter = self._definitions.get(parent or "")
            if getter is not None and self.getter(getter):
                # Attributes of a property are attributes of its value.
                assert isinstance(getter.node, ast.FunctionDef)
                returns = getter.node.returns
                parent = self.annotation(returns, getter.module) if returns else None
            if parent:
                candidate = self._canonical(f"{parent}.{node.attr}", seen)
                if candidate in self._definitions or parent not in self._definitions:
                    return candidate
                method = self.method(parent, node.attr)
                return method.name if method else candidate
        return None

    def infer(
        self,
        node: ast.expr,
        module: str,
        env: SymbolEnvironment,
        *,
        seen: frozenset[str] = frozenset(),
        awaited: bool = False,
    ) -> SymbolValue:
        key = self.key(node)
        if key in env:
            return env[key]
        match node:
            case ast.Await():
                return self.infer(node.value, module, env, seen=seen, awaited=True)
            case ast.Call():
                attribute = self.literal_getattr(node, module, env)
                if attribute is not None:
                    return self.infer(attribute, module, env, seen=seen)
                target = self.infer(node.func, module, env, seen=seen)
                if target.returns and not target.name:
                    return SymbolValue(target.returns, instance=True)
                if (
                    target.name in _COPIES
                    and not target.instance
                    and len(node.args) == 1
                    and not isinstance(node.args[0], ast.Starred)
                ):
                    copied = self.infer(node.args[0], module, env, seen=seen)
                    return SymbolValue(
                        target.name, instance=True, element=copied.element
                    )
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"keys", "values"}
                    and not node.args
                ):
                    mapping = self.infer(node.func.value, module, env, seen=seen)
                    if mapping.name in _MAPPINGS and len(mapping.arguments) == 2:
                        index = 0 if node.func.attr == "keys" else 1
                        return SymbolValue(
                            LIBRARY_VALUE,
                            instance=True,
                            element=mapping.arguments[index],
                        )
                if target.instance and target.name in _SERVICE_SCOPES and not awaited:
                    return SymbolValue(
                        ASYNC_CONTEXT_MANAGER,
                        instance=True,
                        arguments=target.arguments[:1],
                    )
                receiver_type = target.receiver
                definition = self._definitions.get(target.name)
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
                        value = self.typed(
                            definition.node.returns,
                            definition.module,
                            self._type_bindings(definition, target.arguments),
                        )
                        if value.name in {"typing.Self", "typing_extensions.Self"}:
                            value = SymbolValue(receiver_type or "", instance=True)
                        return value if value.name else SymbolValue(instance=True)
                returned = _EXTERNAL_RETURNS.get(target.name)
                if returned and returned[1] == awaited and self.external(target.name):
                    return SymbolValue(returned[0], instance=True)
                if isinstance(node.func, ast.Attribute):
                    receiver = self.infer(node.func.value, module, env, seen=seen)
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
                if not target.instance and self._library_class(target.name):
                    return SymbolValue(target.name, instance=True)
                return SymbolValue()
            case ast.Constant() if node.value is not None:
                return SymbolValue(
                    f"builtins.{type(node.value).__name__}", instance=True
                )
            case ast.JoinedStr():
                return SymbolValue("builtins.str", instance=True)
            case ast.List() | ast.Set():
                return SymbolValue(
                    f"builtins.{type(node).__name__.lower()}",
                    instance=True,
                    element=self._shared(node.elts, module, env, seen=seen),
                )
            case ast.Tuple() | ast.Dict():
                return SymbolValue(
                    f"builtins.{type(node).__name__.lower()}", instance=True
                )
            case ast.ListComp() | ast.SetComp():
                targets = {
                    item.id
                    for generator in node.generators
                    for item in ast.walk(generator.target)
                    if isinstance(item, ast.Name)
                }
                # An element that reads no comprehension variable has the
                # type it has outside the comprehension.
                independent = not any(
                    isinstance(item, ast.Name) and item.id in targets
                    for item in ast.walk(node.elt)
                )
                return SymbolValue(
                    f"builtins.{type(node).__name__[:-4].lower()}",
                    instance=True,
                    element=(
                        self._shared([node.elt], module, env, seen=seen)
                        if independent
                        else ""
                    ),
                )
            case ast.DictComp():
                return SymbolValue("builtins.dict", instance=True)
            case ast.Subscript(value=container, slice=index):
                return self._item(container, index, module, env=env, seen=seen)
            case ast.GeneratorExp():
                return SymbolValue(LIBRARY_VALUE, instance=True)
            case ast.Compare() | ast.UnaryOp(op=ast.Not()):
                return SymbolValue("builtins.bool", instance=True)
            case ast.UnaryOp(operand=operand):
                return self._library_value([operand], module, env, seen=seen)
            case ast.BinOp(left=left, right=right):
                return self._library_value([left, right], module, env, seen=seen)
            case ast.BoolOp(values=values):
                return self._library_value(values, module, env, seen=seen, either=True)
            case ast.IfExp(body=body, orelse=orelse):
                return self._library_value(
                    [body, orelse], module, env, seen=seen, either=True
                )
            case ast.Name() | ast.Attribute():
                if isinstance(node, ast.Name):
                    local = f"{module}.{node.id}"
                    binding = self._bindings.get(local)
                    if isinstance(binding, tuple):
                        if local in seen:
                            return SymbolValue()
                        return self.infer(
                            binding[1], binding[0], {}, seen=seen | {local}
                        )
                name = self.resolve(node, module, env)
                if not name or name in seen:
                    return SymbolValue()
                binding = self._bindings.get(name)
                if isinstance(binding, tuple):
                    return self.infer(binding[1], binding[0], {}, seen=seen | {name})
                if isinstance(node, ast.Attribute):
                    receiver = self.infer(node.value, module, env, seen=seen)
                    getter = self._definitions.get(name)
                    if getter is not None and self.getter(getter):
                        assert isinstance(getter.node, ast.FunctionDef)
                        annotation = getter.node.returns
                        value = (
                            self.typed(
                                annotation,
                                getter.module,
                                self._type_bindings(getter, receiver.arguments),
                            )
                            if annotation
                            else SymbolValue()
                        )
                        if value.name in {"typing.Self", "typing_extensions.Self"}:
                            value = SymbolValue(receiver.name, instance=True)
                        return value
                    field = self.fields(receiver.name).get(node.attr)
                    if field is not None:
                        return field
                    return SymbolValue(
                        name,
                        receiver=receiver.name or None,
                        arguments=receiver.arguments,
                    )
                return SymbolValue(name)
        return SymbolValue()

    def getter(self, function: SourceDefinition) -> bool:
        """Whether *function* is a property getter, including cached ones."""
        return isinstance(function.node, ast.FunctionDef) and any(
            self.resolve(decorator, function.module) in _PROPERTIES
            for decorator in function.node.decorator_list
        )

    def classmethod(self, function: SourceDefinition) -> bool:
        return isinstance(
            function.node, ast.FunctionDef | ast.AsyncFunctionDef
        ) and any(
            self.resolve(decorator, function.module) == "builtins.classmethod"
            for decorator in function.node.decorator_list
        )

    def value(self, node: ast.expr, module: str) -> tuple[str, ast.expr]:
        seen: set[str] = set()
        while name := self.resolve(node, module):
            if name in seen:
                break
            seen.add(name)
            binding = self._bindings.get(name)
            if binding is None or isinstance(binding, str):
                break
            module, node = binding
        return module, node

    def typed(
        self,
        node: ast.expr,
        module: str,
        bindings: Mapping[str, str] | None = None,
    ) -> SymbolValue:
        """The value of an expression annotated with *node*.

        *bindings* substitute the type parameters of a parametrized receiver.
        """
        name = self.annotation(node, module, bindings=bindings)
        if not name:
            returns = self._callable_result(node, module, bindings=bindings)
            return SymbolValue(returns=returns) if returns else SymbolValue()
        return SymbolValue(
            name,
            instance=True,
            element=self.element(node, module, bindings=bindings),
            arguments=self._type_arguments(node, module, bindings=bindings),
        )

    def _callable_result(
        self,
        node: ast.expr,
        module: str,
        *,
        bindings: Mapping[str, str] | None = None,
        seen: frozenset[int] = frozenset(),
    ) -> str:
        # R of a synchronous Callable[..., R], also through a type alias.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return ""
        module, node = self.value(node, module)
        if not isinstance(node, ast.Subscript) or id(node) in seen:
            return ""
        wrapper = self.resolve(node.value, module)
        arguments = (
            node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        )
        if wrapper in _TRANSPARENT_ANNOTATIONS:
            return self._callable_result(
                arguments[0], module, bindings=bindings, seen=seen | {id(node)}
            )
        if wrapper not in _CALLABLES or len(arguments) != 2:
            return ""
        result = arguments[1]
        if isinstance(result, ast.Subscript) and (
            self.resolve(result.value, module) in _AWAITABLES
        ):
            return ""
        return self.annotation(result, module, bindings=bindings) or ""

    def _type_bindings(
        self, method: SourceDefinition, arguments: tuple[str, ...]
    ) -> dict[str, str]:
        # Type parameters of *method*'s class bound to a receiver's arguments.
        if not arguments or method.owner is None:
            return {}
        owner = self._definitions.get(method.owner)
        if owner is None or not isinstance(owner.node, ast.ClassDef):
            return {}
        parameters = [
            parameter.name
            for parameter in owner.node.type_params
            if isinstance(parameter, ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple)
        ]
        for base in owner.node.bases:
            if (
                not parameters
                and isinstance(base, ast.Subscript)
                and self.resolve(base.value, owner.module) in _GENERICS | _PROTOCOLS
            ):
                items = (
                    base.slice.elts
                    if isinstance(base.slice, ast.Tuple)
                    else [base.slice]
                )
                parameters = [item.id for item in items if isinstance(item, ast.Name)]
        if len(parameters) != len(arguments):
            return {}
        return dict(zip(parameters, arguments, strict=True))

    def _type_arguments(
        self,
        node: ast.expr,
        module: str,
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        # Resolved arguments of a parametrized annotation; ``""`` when unknown.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return ()
        if isinstance(node, ast.Name) and bindings and node.id in bindings:
            return ()
        module, node = self.value(node, module)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            members = [
                member
                for member in _union_members(node)
                if not (isinstance(member, ast.Constant) and member.value is None)
            ]
            return (
                self._type_arguments(members[0], module, bindings=bindings)
                if len(members) == 1
                else ()
            )
        if not isinstance(node, ast.Subscript):
            return ()
        wrapper = self.resolve(node.value, module)
        arguments = (
            node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        )
        if wrapper in _TRANSPARENT_ANNOTATIONS:
            return self._type_arguments(arguments[0], module, bindings=bindings)
        if wrapper in _UNIONS:
            members = [
                member
                for member in arguments
                if not (isinstance(member, ast.Constant) and member.value is None)
            ]
            return (
                self._type_arguments(members[0], module, bindings=bindings)
                if len(members) == 1
                else ()
            )
        if wrapper in _OPAQUE_ANNOTATIONS:
            return ()
        return tuple(
            "..."
            if isinstance(item, ast.Constant) and item.value is Ellipsis
            else self.annotation(item, module, bindings=bindings) or ""
            for item in arguments
        )

    def annotation(
        self,
        node: ast.expr,
        module: str,
        seen: frozenset[int] = frozenset(),
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return None
        if isinstance(node, ast.Name) and bindings and node.id in bindings:
            return bindings[node.id] or None
        module, node = self.value(node, module)
        if id(node) in seen:
            return None
        seen = seen | {id(node)}
        if isinstance(node, ast.Subscript):
            wrapper = self.resolve(node.value, module)
            arguments = (
                node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            )
            if wrapper in _TRANSPARENT_ANNOTATIONS:
                return self.annotation(arguments[0], module, seen, bindings=bindings)
            if wrapper in _UNIONS:
                return self._union(arguments, module, seen, bindings=bindings)
            # A parametrized class has the methods of its origin class.
            return None if wrapper in _OPAQUE_ANNOTATIONS else wrapper
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._union(_union_members(node), module, seen, bindings=bindings)
        resolved = self.resolve(node, module)
        return None if resolved in _OPAQUE_ANNOTATIONS else resolved

    def _union(
        self,
        members: list[ast.expr],
        module: str,
        seen: frozenset[int],
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> str | None:
        # Optional[T] after a guard still has T's methods; absence adds no
        # application-defined explicit exceptions.
        names = [
            self.annotation(member, module, seen, bindings=bindings)
            for member in members
            if not (isinstance(member, ast.Constant) and member.value is None)
        ]
        if len(set(names)) == 1:
            return names[0]
        if names and all(name and self.library(name) for name in names):
            return LIBRARY_VALUE
        return None

    def element(
        self,
        node: ast.expr,
        module: str,
        seen: frozenset[int] = frozenset(),
        *,
        bindings: Mapping[str, str] | None = None,
    ) -> str:
        """The type that iterating a value annotated with *node* yields.

        Sequences and iterators yield their item type, mappings their key
        type and homogeneous tuples their shared type.
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return ""
        if isinstance(node, ast.Name) and bindings and node.id in bindings:
            return ""
        module, node = self.value(node, module)
        if id(node) in seen:
            return ""
        seen = seen | {id(node)}
        members = (
            _union_members(node)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
            else None
        )
        if isinstance(node, ast.Subscript):
            wrapper = self.resolve(node.value, module)
            arguments = (
                node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            )
            if wrapper in _TRANSPARENT_ANNOTATIONS:
                return self.element(arguments[0], module, seen, bindings=bindings)
            if wrapper in _UNIONS:
                members = arguments
            elif wrapper in _ITERABLES or wrapper in _MAPPINGS:
                return self.annotation(arguments[0], module, bindings=bindings) or ""
            elif wrapper in _TUPLES:
                shared = {ast.unparse(item) for item in arguments} - {"..."}
                if len(shared) == 1:
                    return (
                        self.annotation(arguments[0], module, bindings=bindings) or ""
                    )
                return ""
        if members is None:
            return ""
        elements = {
            self.element(member, module, seen, bindings=bindings)
            for member in members
            if not (isinstance(member, ast.Constant) and member.value is None)
        }
        return elements.pop() if len(elements) == 1 else ""

    def _shared(
        self,
        elements: list[ast.expr],
        module: str,
        env: SymbolEnvironment,
        *,
        seen: frozenset[str],
    ) -> str:
        # The one instance type that all literal elements share.
        names = {
            value.name if value.instance else ""
            for value in (
                self.infer(item, module, env, seen=seen)
                for item in elements
                if not isinstance(item, ast.Starred)
            )
        }
        return names.pop() if len(names) == 1 and len(elements) else ""

    def _item(
        self,
        container: ast.expr,
        index: ast.expr,
        module: str,
        *,
        env: SymbolEnvironment,
        seen: frozenset[str],
    ) -> SymbolValue:
        # Subscripting an annotated container: a slice keeps the container,
        # a mapping returns its value type, a sequence its element type.
        value = self.infer(container, module, env, seen=seen)
        if not value.instance or not value.name:
            return SymbolValue()
        if isinstance(index, ast.Slice):
            return value if value.name in _ITERABLES | _TUPLES else SymbolValue()
        if value.name in _MAPPINGS:
            item = value.arguments[1] if len(value.arguments) == 2 else ""
        elif value.name in _TUPLES and "..." not in value.arguments:
            position = index.value if isinstance(index, ast.Constant) else None
            item = (
                value.arguments[position]
                if isinstance(position, int)
                and not isinstance(position, bool)
                and -len(value.arguments) <= position < len(value.arguments)
                else ""
            )
        else:
            item = value.element if value.name in _ITERABLES | _TUPLES else ""
        return SymbolValue(item, instance=True) if item else SymbolValue()

    def _library_value(
        self,
        operands: list[ast.expr],
        module: str,
        env: SymbolEnvironment,
        *,
        seen: frozenset[str],
        either: bool = False,
    ) -> SymbolValue:
        # Operators on library values return library values. An expression
        # that selects one of its operands keeps their shared type.
        values = [self.infer(item, module, env, seen=seen) for item in operands]
        names = {value.name for value in values}
        if (
            len(names) == 1
            and all(value.instance for value in values)
            and (either or self.library(values[0].name))
        ):
            return values[0]
        if all(value.instance and self.library(value.name) for value in values):
            return SymbolValue(LIBRARY_VALUE, instance=True)
        return SymbolValue()

    def _library_class(self, name: str) -> bool:
        # Calling a library class constructs a library value. Library
        # functions can return application objects and stay unknown.
        if not name or name in _DYNAMIC or not self.library(name):
            return False
        if name.startswith("builtins.") and name.count(".") == 1:
            value = getattr(builtins, name.split(".")[1], None)
            return isinstance(value, type) and value not in {object, type, super}
        return name.rpartition(".")[2][:1].isupper()

    @property
    def members(self) -> frozenset[str]:
        """Attribute names through which a call can reach application code."""
        return frozenset(self._member_owners())

    def candidates(self, name: str) -> tuple[str, ...]:
        """Scanned methods and attributes that an attribute call *name* can reach."""
        return tuple(sorted(self._member_owners().get(name, ())))

    def _member_owners(self) -> dict[str, list[str]]:
        if self._members is None:
            previous = self._touched
            members: dict[str, list[str]] = {}
            try:
                for definition in list(self._definitions.values()):
                    if definition.owner:
                        members.setdefault(definition.node.name, []).append(
                            definition.name
                        )
                    elif isinstance(definition.node, ast.ClassDef):
                        for attribute in self._callable_attributes(definition):
                            members.setdefault(attribute, []).append(
                                f"{definition.name}.{attribute}"
                            )
            finally:
                self._touched = previous
            self._members = {
                name: list(dict.fromkeys(owners)) for name, owners in members.items()
            }
        return self._members

    def _callable_attributes(self, definition: SourceDefinition) -> list[str]:
        # Class and instance attributes that may hold application callables.
        node = definition.node
        assert isinstance(node, ast.ClassDef)
        result: list[str] = []
        statements: list[tuple[ast.stmt, str | None]] = [
            (statement, None) for statement in node.body
        ]
        for member in node.body:
            if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef) and (
                member.args.posonlyargs or member.args.args
            ):
                instance = [*member.args.posonlyargs, *member.args.args][0].arg
                statements.extend(
                    (statement, instance)
                    for statement in ast.walk(member)
                    if isinstance(statement, ast.Assign | ast.AnnAssign)
                )
        for statement, receiver in statements:
            fields: dict[str, SymbolValue] = {}
            self._field(
                statement, definition.module, {}, receiver=receiver, result=fields
            )
            for name in _assigned_names(statement, receiver):
                known = fields.get(name)
                if known is None or not (known.instance and self.library(known.name)):
                    result.append(name)
        return result

    def key(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if node not in self._keys:
            self._keys[node] = ast.unparse(node)
        return self._keys[node]

    def method(self, owner: str, name: str) -> SourceDefinition | None:
        # Direct methods can be resolved even when an external base is unknown.
        for base in self.mro(owner) or [owner]:
            definition = self._definitions.get(f"{base}.{name}")
            if definition is not None:
                return definition
        return None

    def mro(
        self, name: str, seen: frozenset[str] = frozenset()
    ) -> tuple[str, ...] | None:
        if name in seen:
            return None
        if name in self._mros:
            order, modules = self._mros[name]
            self._touched.update(modules)
            return order
        # Resolution also records modules with uncertain declarations. Replay
        # those dependencies on cache hits so completeness checks stay intact.
        previous = self._touched
        self._touched = set()
        try:
            resolved = self._mro(name, seen)
            order = tuple(resolved) if resolved is not None else None
            self._mros[name] = order, frozenset(self._touched)
            return order
        finally:
            previous.update(self._touched)
            self._touched = previous

    def _provider(self, name: str, seen: frozenset[str] = frozenset()) -> bool:
        definition = self._definitions.get(name)
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
        # A factory that returns a parameter binds the parameter's class.
        assigned: _FactoryEnvironment = {
            arg.arg: (self._class_name(arg.annotation, module),)
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

    def _class_name(self, annotation: ast.expr | None, module: str) -> str | None:
        name = self.annotation(annotation, module) if annotation else None
        definition = self._definitions.get(name or "")
        if definition is not None and isinstance(definition.node, ast.ClassDef):
            return definition.name
        return None

    def _class(self, node: ast.expr, module: str) -> str | None:
        definition = self._definitions.get(self.resolve(node, module) or "")
        if definition is not None and isinstance(definition.node, ast.ClassDef):
            return definition.name
        return None

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

    def _field(
        self,
        statement: ast.stmt,
        module: str,
        env: SymbolEnvironment,
        *,
        receiver: str | None,
        result: dict[str, SymbolValue],
    ) -> None:
        names = _assigned_names(statement, receiver)
        if not names or not isinstance(statement, ast.Assign | ast.AnnAssign):
            return
        value = SymbolValue()
        if isinstance(statement, ast.AnnAssign):
            value = self.typed(statement.annotation, module)
        if not value.name and not value.returns and statement.value is not None:
            value = self.infer(statement.value, module, env)
        if value.name or value.returns:
            result.update(dict.fromkeys(names, value))

    def _locate(self, package: str) -> bool:
        if not package.isidentifier() or package in self._paths:
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
            for root in self._roots:
                if root.is_relative_to(location) or location.is_relative_to(root):
                    return False
        return True


@dataclass(frozen=True, slots=True)
class _Constructions:
    calls: dict[str, list[tuple[SourceDefinition | None, str, ast.Call]]]
    escaped: set[str]
    replaced: set[str] = field(default_factory=set)
    injected: set[str] = field(default_factory=set)


def _defaults(arguments: ast.arguments) -> dict[str, ast.expr]:
    # Default values of a method's parameters after its receiver.
    positional = [item.arg for item in [*arguments.posonlyargs, *arguments.args]]
    defaults = dict(
        zip(
            positional[len(positional) - len(arguments.defaults) :],
            arguments.defaults,
            strict=True,
        )
    )
    defaults.update(
        (item.arg, default)
        for item, default in zip(
            arguments.kwonlyargs, arguments.kw_defaults, strict=True
        )
        if default is not None
    )
    return defaults


def _constructs_receiver(node: ast.Call, site: SourceDefinition | None) -> bool:
    # A method calling its first parameter, its type or its __class__.
    if site is None or not isinstance(
        site.node, ast.FunctionDef | ast.AsyncFunctionDef
    ):
        return False
    first = [*site.node.args.posonlyargs, *site.node.args.args][:1]
    if not first:
        return False
    receiver = first[0].arg
    func = node.func
    return (
        (isinstance(func, ast.Name) and func.id == receiver)
        or (
            isinstance(func, ast.Call)
            and isinstance(func.func, ast.Name)
            and func.func.id == "type"
            and len(func.args) == 1
            and isinstance(func.args[0], ast.Name)
            and func.args[0].id == receiver
        )
        or (
            isinstance(func, ast.Attribute)
            and func.attr == "__class__"
            and isinstance(func.value, ast.Name)
            and func.value.id == receiver
        )
    )


def _executed(body: list[ast.stmt]) -> Iterator[ast.AST]:
    # Nodes of a function body outside nested functions, classes and lambdas.
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
        ):
            pending.extend(ast.iter_child_nodes(node))


def _scope_nodes(statement: ast.stmt) -> Iterator[ast.AST]:
    # Nodes a scope executes: nested function bodies are their own scopes,
    # class bodies belong to the scope that defines them.
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            pending.extend(node.decorator_list)
            pending.extend(node.args.defaults)
            pending.extend(item for item in node.args.kw_defaults if item)
            continue
        pending.extend(ast.iter_child_nodes(node))


def _reference_positions(node: ast.AST) -> Iterator[ast.AST]:
    # Class references that do not hand the class to other code: calls,
    # annotations, type checks, handlers, bases and attribute reads.
    match node:
        case ast.Call(func=func):
            yield func
            if (
                isinstance(func, ast.Name)
                and func.id in {"isinstance", "issubclass"}
                and len(node.args) == 2
            ):
                yield from ast.walk(node.args[1])
        case ast.arg(annotation=annotation) if annotation is not None:
            yield from ast.walk(annotation)
        case ast.FunctionDef() | ast.AsyncFunctionDef() if node.returns is not None:
            yield from ast.walk(node.returns)
        case ast.AnnAssign(annotation=annotation):
            yield from ast.walk(annotation)
        case ast.ExceptHandler(type=handled) if handled is not None:
            yield from ast.walk(handled)
        case ast.ClassDef(bases=bases, keywords=keywords):
            for item in [*bases, *(keyword.value for keyword in keywords)]:
                yield from ast.walk(item)
        case ast.Attribute(value=value, ctx=ast.Load()):
            yield value
        case ast.Raise(exc=raised) if raised is not None:
            yield raised
        case ast.MatchClass(cls=matched):
            yield matched


def _keyword(node: ast.Call, name: str) -> ast.expr | None:
    return next((item.value for item in node.keywords if item.arg == name), None)


def _nested_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    # Statements of *body* and its blocks, without nested scopes.
    for statement in body:
        yield statement
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        for attribute in ("body", "orelse", "finalbody", "handlers", "cases"):
            for child in getattr(statement, attribute, ()):
                if isinstance(child, ast.stmt):
                    yield from _nested_statements([child])
                else:
                    yield from _nested_statements(child.body)


def _assigned_names(statement: ast.stmt, receiver: str | None) -> list[str]:
    # Class-body names without a receiver; `receiver.name` inside a method.
    if isinstance(statement, ast.AnnAssign):
        targets: list[ast.expr] = [statement.target]
    elif isinstance(statement, ast.Assign):
        targets = statement.targets
    else:
        return []
    names: list[str] = []
    for target in targets:
        match target:
            case ast.Name(id=name) if receiver is None:
                names.append(name)
            case ast.Attribute(value=ast.Name(id=owner), attr=name) if (
                owner == receiver
            ):
                names.append(name)
    return names


def _union_members(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return [*_union_members(node.left), *_union_members(node.right)]
    return [node]


def _walk_scopes(tree: ast.Module) -> Iterator[tuple[ast.AST, bool]]:
    pending: deque[tuple[ast.AST, bool]] = deque([(tree, True)])
    while pending:
        node, module_scope = pending.popleft()
        yield node, module_scope
        child_scope = module_scope and not isinstance(
            node,
            ast.FunctionDef
            | ast.AsyncFunctionDef
            | ast.ClassDef
            | ast.Lambda
            | ast.ListComp
            | ast.SetComp
            | ast.DictComp
            | ast.GeneratorExp,
        )
        pending.extend((child, child_scope) for child in ast.iter_child_nodes(node))
