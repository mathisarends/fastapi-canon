"""A deliberately bounded Python symbol index; never imports inspected code."""

import ast
import builtins
import importlib.util
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Self

from fastapi_canon.checks.generator_phases import _has_yield, _phases
from fastapi_canon.checks.library_types import _LibraryTypes
from fastapi_canon.checks.model_values import _Models
from fastapi_canon.checks.symbols import _Definition, _LexicalScope, _scopes
from fastapi_canon.checks.value_flow import (
    _bind,
    _contexts,
    _join,
    _Value,
    _ValueSummary,
)

type _FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
type _Environment = dict[str, _Value]
type _FactoryValue = str | _ForwardedDependency | None
type _FactoryEnvironment = dict[str, tuple[_FactoryValue, ...]]

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
_OPAQUE_ANNOTATIONS = frozenset(
    {
        "builtins.object",
        "builtins.type",
        "collections.abc.Callable",
        "typing.Any",
        "typing_extensions.Any",
        "typing.Callable",
        "typing.Type",
        "typing_extensions.Callable",
        "typing_extensions.Type",
    }
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
_STDLIB_BASES = {
    "sqlite3.Error": "builtins.Exception",
    "sqlite3.Warning": "builtins.Exception",
    "sqlite3.InterfaceError": "sqlite3.Error",
    "sqlite3.DatabaseError": "sqlite3.Error",
    **{
        f"sqlite3.{name}": "sqlite3.DatabaseError"
        for name in (
            "DataError",
            "OperationalError",
            "IntegrityError",
            "InternalError",
            "ProgrammingError",
            "NotSupportedError",
        )
    },
    "asyncio.CancelledError": "builtins.BaseException",
    # datetime is a C-extension re-export on Python 3.12-3.14. Its nominal
    # hierarchy cannot be recovered from the public Python shim's signatures.
    "datetime.datetime": "datetime.date",
    "datetime.date": "builtins.object",
    "datetime.time": "builtins.object",
    "datetime.timedelta": "builtins.object",
    "datetime.timezone": "datetime.tzinfo",
    "datetime.tzinfo": "builtins.object",
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
_EXTERNAL_RETURNS.update(
    {
        name: (returned, False)
        for name, returned in {
            "asyncio.Lock": "asyncio.locks.Lock",
            "asyncio.locks.Lock": "asyncio.locks.Lock",
            "asyncio.Event": "asyncio.locks.Event",
            "asyncio.locks.Event": "asyncio.locks.Event",
            "asyncio.timeout": "asyncio.timeouts.Timeout",
            "asyncio.timeouts.timeout": "asyncio.timeouts.Timeout",
            "pathlib.Path": "pathlib.Path",
            "tempfile.TemporaryDirectory": "tempfile.TemporaryDirectory",
            "contextlib.ExitStack": "contextlib.ExitStack",
            "contextlib.AsyncExitStack": "contextlib.AsyncExitStack",
            "sqlite3.connect": "sqlite3.Connection",
            "sqlite3.dbapi2.connect": "sqlite3.Connection",
            "filelock.FileLock": "filelock.FileLock",
            "urllib.parse.quote": "builtins.str",
            "urllib.parse.quote_plus": "builtins.str",
            "urllib.parse.quote_from_bytes": "builtins.str",
            "urllib.parse.urlencode": "builtins.str",
            "re.sub": "builtins.str",
            "json.dumps": "builtins.str",
        }.items()
    }
)
_EXTERNAL_RETURNS.update(
    {
        name: ("asyncio.subprocess.Process", True)
        for name in (
            "asyncio.create_subprocess_exec",
            "asyncio.create_subprocess_shell",
            "asyncio.subprocess.create_subprocess_exec",
            "asyncio.subprocess.create_subprocess_shell",
        )
    }
)


def _walk_error(error: OSError) -> None:
    raise error


@dataclass(frozen=True)
class _ForwardedDependency:
    name: str


@dataclass(frozen=True)
class _Creation:
    name: str
    dependencies: tuple[str, ...] = ()
    bindings: tuple[tuple[str, _Value], ...] = ()
    call: ast.Call | None = None
    instance: _Value | None = None


@dataclass
class _Source:
    paths: dict[str, Path] = field(default_factory=dict)
    bodies: dict[str, list[ast.stmt]] = field(default_factory=dict)
    definitions: dict[str, _Definition] = field(default_factory=dict)
    bindings: dict[str, tuple[str, ast.expr] | str] = field(default_factory=dict)
    uncertain: dict[str, list[str]] = field(default_factory=dict)
    nested: list[_Definition] = field(default_factory=list)
    touched: set[str] = field(default_factory=set)
    inherited_contracts: list[tuple[str, int]] = field(default_factory=list)
    # raises= and errors= arguments; Composition maps the errors they name.
    error_declarations: list[tuple[str, ast.expr]] = field(default_factory=list)
    roots: list[Path] = field(default_factory=list)
    module_names: set[str] = field(default_factory=set)
    _externals: dict[str, bool] = field(default_factory=dict)
    _methods: frozenset[str] | None = None
    _subclasses: dict[str, tuple[tuple[str, ...], frozenset[str]]] = field(
        default_factory=dict
    )
    _keys: dict[ast.expr, str] = field(default_factory=dict)
    _mros: dict[str, tuple[tuple[str, ...] | None, frozenset[str]]] = field(
        default_factory=dict
    )
    # Port -> (constructed class, forwarded dependency or unknown; source modules).
    _ports: dict[str, list[tuple[_FactoryValue, frozenset[str]]]] | None = None
    _provider_factories: dict[str, list[str | _ForwardedDependency]] = field(
        default_factory=dict
    )
    _providing: set[str] = field(default_factory=set)
    creations: dict[str, list[_Creation]] = field(default_factory=dict)
    _constructing: set[tuple[str, tuple[tuple[str, _Value], ...]]] = field(
        default_factory=set
    )
    _inferring: set[tuple[str, tuple[tuple[str, _Value], ...]]] = field(
        default_factory=set
    )
    _return_sites: dict[tuple[str, tuple[tuple[str, _Value], ...]], str] = field(
        default_factory=dict
    )
    _return_summaries: dict[
        tuple[str, tuple[tuple[str, _Value], ...]], _ValueSummary
    ] = field(default_factory=dict)
    _typing: set[tuple[str, int]] = field(default_factory=set)
    _task_fields: set[tuple[str, str]] = field(default_factory=set)
    heap: dict[str, _Value] = field(default_factory=dict)
    revision: int = 0
    reads: set[str] = field(default_factory=set)
    read_versions: dict[str, _Value | None] = field(default_factory=dict)
    unstable_reads: set[str] = field(default_factory=set)
    _sites: dict[tuple[str, int, int, _Value], str] = field(default_factory=dict)
    libraries: _LibraryTypes = field(default_factory=_LibraryTypes)
    lexical: dict[int, _LexicalScope] = field(default_factory=dict)

    @property
    def models(self) -> _Models:
        return _Models(self)

    def expand(self, value: _Value, seen: frozenset[str] = frozenset()) -> _Value:
        values = []
        for variant in value.variants:
            if variant.name in {"<json>", "<return>"} and variant.site:
                self.observe(variant.site)
                if variant.site not in seen:
                    values.append(
                        self.expand(
                            self.heap.get(variant.site, _Value()), seen | {variant.site}
                        )
                    )
            else:
                values.append(variant)
        return _join(*values)

    def site(
        self, node: ast.expr, module: str, env: _Environment, label: str = ""
    ) -> str:
        key = (
            module,
            getattr(node, "lineno", 0),
            getattr(node, "col_offset", 0),
            _Value(label, bound=env.get("$context", _Value()).reference()),
        )
        if key not in self._sites:
            self._sites[key] = f"{module}:{key[1]}:{key[2]}@{len(self._sites)}"
        return self._sites[key]

    def object(self, value: _Value) -> _Value:
        if value.name in {"<json>", "<return>"}:
            return self.expand(value)
        if value.site:
            self.observe(value.site)
        return self.heap.get(value.site, value)

    def observe(self, site: str) -> None:
        self.reads.add(site)
        value = self.heap.get(site)
        if site in self.read_versions and self.read_versions[site] != value:
            self.unstable_reads.add(site)
        else:
            self.read_versions.setdefault(site, value)

    def element(self, value: _Value) -> _Value:
        results = []
        for variant in value.variants:
            actual = self.object(variant)
            element = actual.element()
            results.append(
                actual.hint.element()
                if element.name == "<empty>" and actual.hint
                else element
            )
        return _join(*results)

    def store(self, value: _Value) -> _Value:
        if not value.site:
            return value
        value = replace(
            value,
            fields=tuple((k, v.reference()) for k, v in value.fields),
            arguments=tuple(v.reference() for v in value.arguments),
        )
        old = self.heap.get(value.site)
        if old:
            first, second = dict(old.fields), dict(value.fields)
            fields = tuple(
                sorted(
                    (
                        name,
                        _join(first[name], second[name])
                        if name in first and name in second
                        else first.get(name, second.get(name, _Value())),
                    )
                    for name in first.keys() | second.keys()
                )
            )
            arguments = (
                tuple(
                    _join(a, b)
                    for a, b in zip(old.arguments, value.arguments, strict=True)
                )
                if len(old.arguments) == len(value.arguments)
                else value.arguments or old.arguments
            )
            value = replace(
                value,
                fields=fields,
                arguments=arguments,
                initialized=old.initialized or value.initialized,
                keys_known=old.keys_known and value.keys_known,
            )
        if value != old:
            self.heap[value.site] = value
            self.revision += 1
        return value.reference()

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
                    text = path.read_text("utf-8-sig")
                    tree = ast.parse(text, filename=str(path))
                    source.bodies[module] = tree.body
                    source.lexical.update(_scopes(tree, text, str(path)))
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
                                _Definition(
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
                if (
                    not factory.owner
                    and isinstance(node, ast.FunctionDef)
                    and not node.decorator_list
                ):
                    self._index_route_prefix(factory)
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

    def _index_route_prefix(self, factory: _Definition) -> None:
        node = factory.node
        assert isinstance(node, ast.FunctionDef)
        prefix: list[ast.stmt] = []
        routers: set[str] = set()
        functions = []
        prefix_end = 0
        for index, statement in enumerate(node.body):
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and isinstance(statement.value, ast.Call)
                and self.resolve(statement.value.func, factory.module)
                in _ROUTER_DECLARATIONS
            ):
                routers.add(statement.targets[0].id)
                prefix.append(statement)
            elif (
                isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
                and routers
            ):
                functions.append(statement)
                prefix.append(statement)
            elif isinstance(statement, ast.Pass) or (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                continue
            else:
                break
            prefix_end = index + 1
        if not functions:
            return
        parameters = (
            {
                arg.arg
                for arg in [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ]
            }
            | ({node.args.vararg.arg} if node.args.vararg else set())
            | ({node.args.kwarg.arg} if node.args.kwarg else set())
        )
        locals_ = {
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        } | {
            child.name
            for child in ast.walk(node)
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }
        declarations = routers | {fn.name for fn in functions}
        for function in functions:
            own = {
                arg.arg
                for arg in [
                    *function.args.posonlyargs,
                    *function.args.args,
                    *function.args.kwonlyargs,
                ]
            } | {
                child.id
                for child in ast.walk(function)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
            }
            free = {
                child.id
                for statement in function.body
                for child in ast.walk(statement)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
            } - own
            if free & ((parameters | locals_) - declarations) or free & routers:
                return
        remaining = node.body[prefix_end:]
        if any(
            (
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Store | ast.Del)
                and child.id in declarations
            )
            or (
                isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and child.name in declarations
            )
            for statement in remaining
            for child in ast.walk(statement)
        ):
            return
        scope = f"{factory.name}.<locals>"
        self.paths[scope] = self.paths[factory.module]
        for name in self.bindings.keys() | self.definitions.keys():
            parent, _, local = name.rpartition(".")
            if parent == factory.module and local not in parameters | locals_:
                self.bindings[f"{scope}.{local}"] = name
        names: set[str] = set()
        self._index(scope, prefix, package=False, names=names)
        if not all(
            self._route_factory_value(statement.value, scope)
            for statement in prefix
            if isinstance(statement, ast.Assign)
        ):
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
            return
        self.uncertain.setdefault(scope, []).extend(
            self.uncertain.get(factory.module, [])
        )
        indexed = {id(self.definitions[name].node) for name in names}
        self.nested = [
            definition
            for definition in self.nested
            if id(definition.node) not in indexed
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
                        self.module_names.add(alias.name)
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
                    self.definitions[name] = _Definition(name, module, node)
                    names.add(name)
                    if isinstance(node, ast.ClassDef):
                        for member in node.body:
                            if isinstance(
                                member, ast.FunctionDef | ast.AsyncFunctionDef
                            ):
                                key = f"{name}.{member.name}"
                                self.definitions[key] = _Definition(
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
        # Resolve aliases within a namespace, never across a source module boundary.
        # A package can export `router` from `router.py`: that package attribute
        # must not rewrite the module prefix of `package.router.endpoint`.
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
        env: _Environment | None = None,
        *,
        seen: frozenset[str] = frozenset(),
    ) -> str | None:
        self.touched.add(module)
        env = env or {}
        if env:
            text = self.key(node)
            if text in env:
                return self.infer(node, module, env, seen).name or None
        if isinstance(node, ast.Name):
            local = f"{module}.{node.id}"
            if local in self.bindings or local in self.definitions:
                return self.canonical(local, seen)
            if hasattr(builtins, node.id):
                return f"builtins.{node.id}"
        elif isinstance(node, ast.Attribute):
            return self.infer(node, module, env, seen).name or None
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
        name = self.resolve(node, module)
        return None if name in _OPAQUE_ANNOTATIONS else name

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
        if name == "builtins.NoneType":
            return [name, "builtins.object"]
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
        bases: list[str | None]
        if name in _STDLIB_BASES:
            order = self.mro(_STDLIB_BASES[name], seen | {name})
            return [name, *order] if order else None
        if name in {
            "pydantic.ValidationError",
            "pydantic_core.ValidationError",
            "pydantic_core._pydantic_core.ValidationError",
            "pydantic_core.PydanticSerializationError",
            "pydantic_core._pydantic_core.PydanticSerializationError",
        }:
            return [
                name,
                "builtins.ValueError",
                "builtins.Exception",
                "builtins.BaseException",
                "builtins.object",
            ]
        definition = self.definitions.get(name)
        if definition is None or not isinstance(definition.node, ast.ClassDef):
            bases = (
                list(self.libraries.bases(name) or ()) if self.external(name) else []
            )
            if not bases:
                return None
        else:
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

    def method(self, owner: str, name: str) -> _Definition | None:
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

    def classmethod(self, function: _Definition) -> bool:
        return isinstance(
            function.node, ast.FunctionDef | ast.AsyncFunctionDef
        ) and any(
            self.resolve(decorator, function.module) == "builtins.classmethod"
            for decorator in function.node.decorator_list
        )

    def condition(self, node: ast.expr, module: str, env: _Environment) -> bool | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            result = self.condition(node.operand, module, env)
            return not result if result is not None else None
        if (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Eq | ast.NotEq)
        ):
            left, right = (
                self.infer(node.left, module, env),
                self.infer(node.comparators[0], module, env),
            )
            if (
                left.name
                in {
                    "builtins.str",
                    "builtins.int",
                    "builtins.bool",
                    "builtins.float",
                    "builtins.NoneType",
                }
                and right.name
                in {
                    "builtins.str",
                    "builtins.int",
                    "builtins.bool",
                    "builtins.float",
                    "builtins.NoneType",
                }
                and (left.literal is not None or left.name == "builtins.NoneType")
                and (right.literal is not None or right.name == "builtins.NoneType")
            ):
                equal = left.literal == right.literal
                return equal if isinstance(node.ops[0], ast.Eq) else not equal
        if (
            isinstance(node, ast.Call)
            and self.resolve(node.func, module, env) == "builtins.hasattr"
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            answers = []
            for value in self.infer(node.args[0], module, env).variants:
                for name in [
                    value.name,
                    *([] if value.exact else self.subclasses(value.name)),
                ]:
                    if name in self.definitions:
                        answers.append(
                            True
                            if self.model(name)
                            and node.args[1].value in {"model_dump", "model_dump_json"}
                            and self.method(name, "__getattribute__") is None
                            else None
                        )
                    else:
                        answers.append(
                            self.libraries.presence(name, node.args[1].value)
                        )
            if answers and all(answer is True for answer in answers):
                return True
            if answers and all(answer is False for answer in answers):
                return False
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
            targets = (
                node.args[1].elts
                if isinstance(node.args[1], ast.Tuple)
                else [node.args[1]]
            )
            names = [self.resolve(t, module, env) for t in targets]
            answers = [self.membership(v, names) for v in value.variants]
            if answers and all(answer is True for answer in answers):
                return True
            if answers and all(answer is False for answer in answers):
                return False
        return None

    def membership(self, value: _Value, targets: list[str | None]) -> bool | None:
        aliases = {
            "typing.Mapping": "collections.abc.Mapping",
            "typing.Sequence": "collections.abc.Sequence",
        }
        targets = [
            aliases.get(name or "", self.libraries.canonical(name or ""))
            for name in targets
        ]
        nominal = {
            "builtins.str",
            "builtins.int",
            "builtins.float",
            "builtins.bool",
            "builtins.bytes",
            "builtins.dict",
            "builtins.list",
            "builtins.tuple",
            "builtins.set",
            "builtins.frozenset",
            "builtins.object",
            "pydantic.main.BaseModel",
            "pydantic.root_model.RootModel",
            "enum.Enum",
            "collections.abc.Mapping",
            "collections.abc.Sequence",
        }
        if not all(name in nominal for name in targets):
            return None
        answers = []
        if not value.exact and any(
            self.libraries.canonical(value.name)
            in {self.libraries.canonical(base) for base in self.mro(target or "") or ()}
            and self.libraries.canonical(value.name) != target
            for target in targets
        ):
            return None
        for name in [value.name, *([] if value.exact else self.subclasses(value.name))]:
            order = self.mro(name)
            if order is None:
                return None
            bases = {
                aliases.get(base, self.libraries.canonical(base)) for base in order
            }
            if "builtins.dict" in bases:
                bases.add("collections.abc.Mapping")
            if bases & {
                "builtins.list",
                "builtins.tuple",
                "builtins.str",
                "builtins.bytes",
            }:
                bases.add("collections.abc.Sequence")
            answers.append(bool(bases & set(targets)))
        return (
            True
            if answers and all(answers)
            else False
            if answers and not any(answers)
            else None
        )

    def narrow(
        self, node: ast.expr, module: str, env: _Environment, truth: bool
    ) -> _Environment:
        result = env.copy()
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self.narrow(node.operand, module, env, not truth)
        if isinstance(node, ast.BoolOp):
            together = isinstance(node.op, ast.And) == truth
            if together:
                for child in node.values:
                    result = self.narrow(child, module, result, truth)
                return result
            branches = [self.narrow(child, module, env, truth) for child in node.values]
            self.merge_environments(result, branches)
            return result
        if (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Is | ast.IsNot)
            and isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value is None
        ):
            value = self.infer(node.left, module, env)
            keep_none = truth == isinstance(node.ops[0], ast.Is)
            variants = [
                v
                for v in value.variants
                if (v.name == "builtins.NoneType") == keep_none
            ]
            if variants:
                result[f"$narrow:{self.key(node.left)}"] = _join(*variants)
        if (
            isinstance(node, ast.Call)
            and self.resolve(node.func, module, env) == "builtins.isinstance"
            and len(node.args) == 2
            and not node.keywords
        ):
            target = node.args[1]
            types = target.elts if isinstance(target, ast.Tuple) else [target]
            names = [self.resolve(t, module, env) for t in types]
            value = self.infer(node.args[0], module, env)
            refined: list[_Value] = []
            for variant in value.variants:
                membership = self.membership(variant, names)
                matches = any(
                    name in (self.mro(variant.name) or [variant.name]) for name in names
                )
                if not variant.name and truth:
                    refined.extend(
                        _Value(name, instance=True) for name in names if name
                    )
                elif (
                    membership == truth
                    if membership is not None
                    else matches == truth or not variant.exact
                ):
                    refined.append(variant)
            if refined:
                result[f"$narrow:{self.key(node.args[0])}"] = _join(*refined)
        return result

    def merge_environments(
        self, env: _Environment, branches: list[_Environment]
    ) -> None:
        if not branches:
            return
        for name in set().union(*(branch.keys() for branch in branches)):
            if name.startswith("$narrow:") and any(
                name not in branch for branch in branches
            ):
                env.pop(name, None)
                continue
            env[name] = _join(*(branch.get(name, _Value()) for branch in branches))

    def infer(
        self,
        node: ast.expr,
        module: str,
        env: _Environment,
        seen: frozenset[str] = frozenset(),
        *,
        awaited: bool = False,
    ) -> _Value:
        key = self.key(node)
        if f"$narrow:{key}" in env:
            return env[f"$narrow:{key}"]
        if key in env and (
            not isinstance(node, ast.Attribute)
            or not any(
                value.site
                for value in self.infer(node.value, module, env, seen).variants
            )
        ):
            return self.expand(env[key])
        match node:
            case ast.Constant():
                return _Value(
                    f"builtins.{type(node.value).__name__}",
                    instance=True,
                    exact=True,
                    literal=node.value
                    if isinstance(node.value, str | int | float | bool)
                    else None,
                )
            case ast.Tuple() | ast.List() | ast.Set():
                elements_list: list[_Value] = []
                variadic = False
                for tuple_item in node.elts:
                    value = self.infer(
                        tuple_item.value
                        if isinstance(tuple_item, ast.Starred)
                        else tuple_item,
                        module,
                        env,
                        seen,
                    )
                    if isinstance(tuple_item, ast.Starred):
                        actual = self.object(value)
                        if actual.name == "builtins.tuple" and not actual.variadic:
                            elements_list.extend(actual.arguments)
                        else:
                            elements_list.append(self.element(value))
                            variadic = True
                    else:
                        elements_list.append(value)
                elements = tuple(elements_list)
                result = _Value(
                    f"builtins.{type(node).__name__.lower()}",
                    instance=True,
                    exact=True,
                    variadic=variadic,
                    arguments=elements
                    if isinstance(node, ast.Tuple)
                    else (_join(*elements),),
                )
                return (
                    result
                    if isinstance(node, ast.Tuple)
                    else self.store(
                        replace(result, site=self.site(node, module, env, result.name))
                    )
                )
            case ast.Dict():
                keys = tuple(
                    self.infer(key, module, env, seen) for key in node.keys if key
                )
                values = tuple(
                    self.infer(value, module, env, seen) for value in node.values
                )
                fields = tuple(
                    (str(key.literal), value)
                    for key, value in zip(keys, values, strict=False)
                    if isinstance(key.literal, str)
                )
                return self.store(
                    _Value(
                        "builtins.dict",
                        instance=True,
                        exact=True,
                        arguments=(_join(*keys), _join(*values)),
                        fields=fields,
                        keys_known=all(
                            isinstance(key, ast.Constant) and isinstance(key.value, str)
                            for key in node.keys
                        ),
                        site=self.site(node, module, env, "builtins.dict"),
                    )
                )
            case ast.JoinedStr():
                return _Value("builtins.str", instance=True, exact=True)
            case ast.BinOp():
                left = self.infer(node.left, module, env, seen)
                right = self.infer(node.right, module, env, seen)
                results = []
                for first in left.variants:
                    for second in right.variants:
                        if first.name in {
                            "pathlib.Path",
                            "pathlib.PosixPath",
                            "pathlib.WindowsPath",
                        } and isinstance(node.op, ast.Div):
                            results.append(_Value("pathlib.Path", instance=True))
                        elif first.name == second.name and first.name in {
                            "builtins.str",
                            "builtins.int",
                            "builtins.float",
                            "builtins.bytes",
                        }:
                            results.append(replace(first, literal=None))
                        elif (
                            first.name == "builtins.NoneType"
                            or second.name == "builtins.NoneType"
                        ):
                            results.append(_Value("<empty>"))
                        else:
                            results.append(_Value())
                return _join(*results)
            case ast.IfExp():
                condition = self.condition(node.test, module, env)
                return _join(
                    *(
                        self.infer(
                            value,
                            module,
                            self.narrow(node.test, module, env, value is node.body),
                            seen,
                        )
                        for value in (
                            [node.body]
                            if condition is True
                            else [node.orelse]
                            if condition is False
                            else [node.body, node.orelse]
                        )
                    )
                )
            case ast.BoolOp():
                return _join(
                    *(self.infer(value, module, env, seen) for value in node.values)
                )
            case ast.NamedExpr():
                value = self.infer(node.value, module, env, seen)
                self.assign(node.target, value, env)
                return value
            case ast.ListComp() | ast.SetComp() | ast.DictComp() | ast.GeneratorExp():
                comprehension_env = env.copy()
                for generator in node.generators:
                    element = self.element(
                        self.infer(generator.iter, module, comprehension_env, seen)
                    )
                    if element.name == "<empty>":
                        empty_name = (
                            "builtins.dict"
                            if isinstance(node, ast.DictComp)
                            else "builtins.list"
                            if isinstance(node, ast.ListComp)
                            else "builtins.set"
                            if isinstance(node, ast.SetComp)
                            else "collections.abc.Iterator"
                        )
                        return self.store(
                            _Value(
                                empty_name,
                                instance=True,
                                exact=True,
                                arguments=(element, element)
                                if isinstance(node, ast.DictComp)
                                else (element,),
                                keys_known=isinstance(node, ast.DictComp),
                                site=self.site(node, module, env, empty_name),
                            )
                        )
                    self.assign(
                        generator.target,
                        self.element(
                            self.infer(generator.iter, module, comprehension_env, seen)
                        ),
                        comprehension_env,
                    )
                if isinstance(node, ast.DictComp):
                    comprehension_arguments: tuple[_Value, ...] = (
                        self.infer(node.key, module, comprehension_env, seen),
                        self.infer(node.value, module, comprehension_env, seen),
                    )
                    name = "builtins.dict"
                else:
                    comprehension_arguments = (
                        self.infer(node.elt, module, comprehension_env, seen),
                    )
                    name = (
                        "builtins.list"
                        if isinstance(node, ast.ListComp)
                        else "builtins.set"
                        if isinstance(node, ast.SetComp)
                        else "collections.abc.Iterator"
                    )
                return self.store(
                    _Value(
                        name,
                        instance=True,
                        exact=True,
                        arguments=comprehension_arguments,
                        site=self.site(node, module, env, name),
                    )
                )
            case ast.Subscript():
                value = self.infer(node.value, module, env, seen)
                results = []
                for item in value.variants:
                    item = self.object(item)
                    key_value = self.infer(node.slice, module, env, seen)
                    if (
                        item.name == "builtins.dict"
                        and isinstance(key_value.literal, str)
                        and key_value.literal in dict(item.fields)
                    ):
                        results.append(dict(item.fields)[key_value.literal])
                        continue
                    if item.name == "builtins.dict" and len(item.arguments) == 2:
                        results.append(item.arguments[1])
                    elif item.name in {"builtins.list", "builtins.tuple"}:
                        if isinstance(node.slice, ast.Slice):
                            results.append(item)
                        elif (
                            item.name == "builtins.tuple"
                            and isinstance(node.slice, ast.Constant)
                            and isinstance(node.slice.value, int)
                            and -len(item.arguments)
                            <= node.slice.value
                            < len(item.arguments)
                        ):
                            results.append(item.arguments[node.slice.value])
                        else:
                            results.append(item.element())
                    else:
                        results.append(_Value())
                return _join(*results)
            case ast.Await():
                deferred = self.coroutine(self.infer(node.value, module, env, seen))
                if deferred and deferred.name == "<coroutine>" and deferred.bound:
                    definition = self.definitions.get(deferred.bound.name)
                    if definition:
                        return self.returned(definition, dict(deferred.fields))
                return self.infer(node.value, module, env, seen, awaited=True)
            case ast.Call():
                target = self.infer(node.func, module, env, seen)
                return self.expand(
                    _join(
                        *(
                            self.called(
                                node, variant, module, env, seen, awaited=awaited
                            )
                            for variant in target.variants
                        )
                    )
                )
            case ast.Attribute():
                receiver = self.infer(node.value, module, env, seen)
                return self.expand(
                    _join(
                        *(self.member(value, node.attr) for value in receiver.variants)
                    )
                )
            case ast.Name():
                if isinstance(node, ast.Name):
                    local = f"{module}.{node.id}"
                    binding = self.bindings.get(local)
                    if isinstance(binding, tuple):
                        if local in seen:
                            return _Value()
                        return self.infer(binding[1], binding[0], {}, seen | {local})
                resolved_name = self.resolve(node, module, env)
                if not resolved_name or resolved_name in seen:
                    return _Value()
                binding = self.bindings.get(resolved_name)
                if isinstance(binding, tuple):
                    return self.infer(
                        binding[1], binding[0], {}, seen | {resolved_name}
                    )
                definition = self.definitions.get(resolved_name)
                return _Value(
                    resolved_name,
                    exact=bool(
                        definition and isinstance(definition.node, ast.ClassDef)
                    ),
                )
        return _Value()

    def called(
        self,
        node: ast.Call,
        target: _Value,
        module: str,
        env: _Environment,
        seen: frozenset[str] = frozenset(),
        *,
        awaited: bool = False,
    ) -> _Value:
        if target.name == "builtins.type" and len(node.args) == 1 and not node.keywords:
            return _join(
                *(
                    _Value(value.name, exact=value.exact) if value.name else _Value()
                    for value in self.infer(node.args[0], module, env, seen).variants
                )
            )
        if (
            target.name == "builtins.getattr"
            and len(node.args) in {2, 3}
            and not node.keywords
        ):
            receiver = self.infer(node.args[0], module, env, seen)
            names = self.infer(node.args[1], module, env, seen)
            results = []
            for value in receiver.variants:
                for attribute in names.variants:
                    if isinstance(attribute.literal, str):
                        results.append(self.member(value, attribute.literal))
                    else:
                        results.append(_Value())
            if len(node.args) == 3:
                results.append(self.infer(node.args[2], module, env, seen))
            return _join(*results)
        if (
            target.name
            in {
                "asyncio.open_connection",
                "asyncio.streams.open_connection",
                "asyncio.open_unix_connection",
                "asyncio.streams.open_unix_connection",
            }
            and awaited
        ):
            return _Value(
                "builtins.tuple",
                instance=True,
                exact=True,
                arguments=(
                    _Value("asyncio.StreamReader", instance=True),
                    _Value("asyncio.StreamWriter", instance=True),
                ),
            )
        if target.name == "<empty>":
            return target
        if target.name in {
            "builtins.str",
            "builtins.int",
            "builtins.float",
            "builtins.bool",
            "builtins.bytes",
        }:
            return _Value(target.name, instance=True, exact=True)
        if target.name in {
            "builtins.iter",
            "builtins.next",
            "builtins.sorted",
            "builtins.range",
            "builtins.enumerate",
            "os.walk",
        }:
            value = self.infer(node.args[0], module, env) if node.args else _Value()
            if target.name == "builtins.next":
                return _join(
                    self.element(value),
                    self.infer(node.args[1], module, env)
                    if len(node.args) > 1
                    else _Value("<empty>"),
                )
            intrinsic_element = self.element(value)
            if target.name == "builtins.range":
                intrinsic_element = _Value("builtins.int", instance=True, exact=True)
            if target.name == "builtins.enumerate":
                intrinsic_element = _Value(
                    "builtins.tuple",
                    instance=True,
                    exact=True,
                    arguments=(
                        _Value("builtins.int", instance=True, exact=True),
                        intrinsic_element,
                    ),
                )
            if target.name == "os.walk":
                text = _Value("builtins.str", instance=True, exact=True)
                strings = _Value(
                    "builtins.list", instance=True, exact=True, arguments=(text,)
                )
                intrinsic_element = _Value(
                    "builtins.tuple",
                    instance=True,
                    exact=True,
                    arguments=(text, strings, strings),
                )
            return _Value(
                "builtins.list"
                if target.name == "builtins.sorted"
                else "collections.abc.Iterator",
                instance=True,
                exact=True,
                arguments=(intrinsic_element,),
            )
        if (
            target.name in {"asyncio.create_task", "asyncio.tasks.create_task"}
            and node.args
        ):
            coroutine = self.infer(node.args[0], module, env, seen)
            return _Value(
                "asyncio.Task", instance=True, exact=True, arguments=(coroutine,)
            )
        if (
            target.name
            in {
                "asyncio.shield",
                "asyncio.tasks.shield",
                "asyncio.wait_for",
                "asyncio.tasks.wait_for",
            }
            and node.args
        ):
            return self.infer(node.args[0], module, env, seen, awaited=awaited)
        if (
            target.name in {"asyncio.to_thread", "asyncio.threads.to_thread"}
            and node.args
        ):
            invocation = ast.copy_location(
                ast.Call(
                    func=node.args[0],
                    args=node.args[1:],
                    keywords=node.keywords,
                ),
                node,
            )
            callback = self.infer(node.args[0], module, env, seen)
            results = []
            for variant in callback.variants:
                definition = self.definitions.get(variant.name)
                if definition is None:
                    results.append(_Value())
                    continue
                bindings = self.call_environment(
                    definition, invocation, module, env, variant.bound
                )
                if awaited:
                    results.append(self.returned(definition, bindings))
                else:
                    results.append(
                        _Value(
                            "<coroutine>",
                            bound=variant,
                            fields=tuple(sorted((bindings or {}).items())),
                            site=self.site(node, module, env, "deferred"),
                        )
                    )
            return _join(*results)
        if target.name in {
            "builtins.dict",
            "builtins.list",
            "builtins.set",
            "builtins.tuple",
        }:
            container_value = (
                self.object(self.infer(node.args[0], module, env, seen))
                if node.args
                else _Value("<empty>")
            )
            container_arguments = (
                container_value.arguments
                if target.name == "builtins.dict"
                and container_value.name == "builtins.dict"
                else (self.element(container_value),)
                if node.args
                else (_Value("<empty>"), _Value("<empty>"))
                if target.name == "builtins.dict"
                else (_Value("<empty>"),)
            )
            container_fields = (
                dict(container_value.fields) if target.name == "builtins.dict" else {}
            )
            known_keys = container_value.keys_known
            if target.name == "builtins.dict":
                if node.args and container_value.name != "builtins.dict":
                    pairs = [
                        self.object(v) for v in self.element(container_value).variants
                    ]
                    if all(
                        pair.name == "builtins.tuple" and len(pair.arguments) == 2
                        for pair in pairs
                    ):
                        container_arguments = (
                            _join(*(pair.arguments[0] for pair in pairs)),
                            _join(*(pair.arguments[1] for pair in pairs)),
                        )
                        known_keys = all(
                            isinstance(pair.arguments[0].literal, str) for pair in pairs
                        )
                        if known_keys:
                            for pair in pairs:
                                key = str(pair.arguments[0].literal)
                                container_fields[key] = _join(
                                    container_fields.get(key, _Value("<empty>")),
                                    pair.arguments[1],
                                )
                    else:
                        container_arguments = (_Value(), _Value())
                        known_keys = False
                elif not node.args:
                    known_keys = True
                for keyword in node.keywords:
                    if keyword.arg is None:
                        known_keys = False
                    else:
                        container_fields[keyword.arg] = self.infer(
                            keyword.value, module, env
                        )
                if container_fields:
                    container_arguments = (
                        _join(
                            container_arguments[0],
                            *(
                                _Value(
                                    "builtins.str",
                                    instance=True,
                                    exact=True,
                                    literal=name,
                                )
                                for name in container_fields
                            ),
                        ),
                        _join(container_arguments[1], *container_fields.values()),
                    )
            return self.store(
                _Value(
                    target.name,
                    instance=True,
                    exact=True,
                    arguments=container_arguments,
                    fields=tuple(container_fields.items()),
                    keys_known=known_keys if target.name == "builtins.dict" else False,
                    site=self.site(node, module, env, target.name),
                )
            )
        if target.name == "builtins.super":
            owner = (
                self.infer(node.args[0], module, env)
                if len(node.args) == 2
                else env.get("$owner", _Value())
            )
            bound = (
                self.infer(node.args[1], module, env)
                if len(node.args) == 2
                else env.get("$self", _Value())
            )
            return _Value("<super>", receiver=owner.name, bound=bound)
        receiver_type = target.receiver
        definition = self.definitions.get(target.name)
        if definition and isinstance(definition.node, ast.ClassDef):
            if not target.instance:
                return self.construct(definition.name, node, module, env)
            receiver_type = definition.name
            definition = self.method(definition.name, "__call__")
            if definition:
                target = _Value(definition.name, receiver=receiver_type, bound=target)
        if definition and isinstance(
            definition.node, ast.FunctionDef | ast.AsyncFunctionDef
        ):
            actual = self.call_environment(
                definition, node, module, env, target.bound, captures=target.fields
            )
            context_decorators = {
                self.resolve(d, definition.module)
                for d in definition.node.decorator_list
            }
            if context_decorators & {
                "contextlib.contextmanager",
                "contextlib.asynccontextmanager",
            }:
                return _Value(
                    "<contextmanager>",
                    bound=target,
                    fields=tuple(sorted((actual or {}).items())),
                    site=self.site(node, module, env, "contextmanager"),
                )
            if isinstance(definition.node, ast.AsyncFunctionDef) and not awaited:
                return _Value(
                    "<coroutine>",
                    bound=target,
                    fields=tuple(sorted((actual or {}).items())),
                    site=self.site(node, module, env, "deferred"),
                )
            if awaited and not isinstance(definition.node, ast.AsyncFunctionDef):
                return _Value()
            returned = (
                self.returned(definition, actual) if actual is not None else _Value()
            )
            if returned.name or returned.alternatives:
                return returned
            if definition.node.returns:
                value = self.typed(definition.node.returns, definition.module)
                if value.name in {"typing.Self", "typing_extensions.Self"}:
                    return _Value(receiver_type or "", instance=True)
                return replace(value, unresolved=True)
        if (
            target.bound
            and self.libraries.canonical(target.bound.name)
            == "pydantic.type_adapter.TypeAdapter"
        ):
            adapter_method = target.name.rsplit(".", 1)[-1]
            adapter_site = self.site(node, module, env, adapter_method)
            schema = dict(target.bound.fields).get("$schema", _Value())
            data = self.infer(node.args[0], module, env) if node.args else _Value()
            if adapter_method in {
                "validate_python",
                "validate_json",
                "validate_strings",
            }:
                if adapter_method == "validate_json":
                    data = self.models.json(adapter_site + ":input")
                if self.model(schema.name):
                    return self.models.validate(schema.name, data, adapter_site)
                return _Value()
            if adapter_method == "dump_python":
                adapter_mode = _keyword(node, "mode")
                if (
                    adapter_mode
                    and self.infer(adapter_mode, module, env).literal == "json"
                ):
                    return self.models.json(adapter_site)
            if adapter_method == "dump_json":
                return _Value("builtins.bytes", instance=True, exact=True)
        if (
            target.bound
            and self.model(target.bound.name)
            and target.name not in self.definitions
        ):
            model_method = target.name.rsplit(".", 1)[-1]
            site = self.site(node, module, env, model_method)
            if model_method in {
                "model_validate",
                "model_validate_json",
                "model_validate_strings",
            }:
                data = self.infer(node.args[0], module, env) if node.args else _Value()
                if model_method == "model_validate_json":
                    data = self.models.json(site + ":input")
                return self.models.validate(target.bound.name, data, site)
            if model_method == "model_copy":
                update = _keyword(node, "update")
                return self.models.copy(
                    target.bound,
                    self.infer(update, module, env)
                    if update
                    else _Value("builtins.NoneType", instance=True),
                    site,
                )
            if model_method == "model_dump":
                dump_mode = _keyword(node, "mode")
                return self.models.dump(
                    target.bound,
                    json=bool(
                        dump_mode
                        and self.infer(dump_mode, module, env).literal == "json"
                    ),
                    site=site,
                )
            if model_method == "model_dump_json":
                return _Value("builtins.str", instance=True, exact=True)
        external_return = _EXTERNAL_RETURNS.get(target.name)
        if (
            external_return
            and external_return[1] == awaited
            and self.external(target.name)
        ):
            if target.name in {"sqlite3.connect", "sqlite3.dbapi2.connect"} and (
                len(node.args) > 5
                or any(kw.arg in {None, "factory"} for kw in node.keywords)
            ):
                return _Value()
            result = _Value(
                external_return[0], instance=True, exact=True, initialized=True
            )
            if external_return[0] in {
                "contextlib.ExitStack",
                "contextlib.AsyncExitStack",
            }:
                return self.store(
                    replace(result, site=self.site(node, module, env, result.name))
                )
            return result
        if isinstance(node.func, ast.Attribute):
            receiver = self.object(
                target.bound or self.infer(node.func.value, module, env, seen)
            )
            if (
                receiver.name
                in {"pathlib.Path", "pathlib.PosixPath", "pathlib.WindowsPath"}
                and node.func.attr == "open"
            ):
                mode_node = node.args[0] if node.args else _keyword(node, "mode")
                mode = self.infer(mode_node, module, env).literal if mode_node else "r"
                return _Value(
                    "io.BufferedRandom"
                    if isinstance(mode, str) and "b" in mode
                    else "io.TextIOWrapper",
                    instance=True,
                    exact=True,
                )
            if receiver.name in {
                "pathlib.Path",
                "pathlib.PosixPath",
                "pathlib.WindowsPath",
            } and node.func.attr in {
                "resolve",
                "absolute",
                "expanduser",
                "with_name",
                "with_suffix",
                "joinpath",
            }:
                return _Value("pathlib.Path", instance=True)
            if (
                receiver.name in {"contextlib.ExitStack", "contextlib.AsyncExitStack"}
                and node.func.attr in {"enter_context", "enter_async_context"}
                and node.args
            ):
                resource = self.infer(node.args[0], module, env, seen)
                if resource.name == "<contextmanager>" and resource.bound:
                    definition = self.definitions.get(resource.bound.name)
                    if definition:
                        return self.returned(
                            definition, dict(resource.fields), yielded=True
                        )
                method = self.member(
                    resource,
                    "__aenter__"
                    if node.func.attr == "enter_async_context"
                    else "__enter__",
                )
                resource_return = self.libraries.returned(
                    method, awaited=node.func.attr == "enter_async_context"
                )
                if resource_return:
                    return resource_return
            if receiver.name in {
                "pathlib.Path",
                "pathlib.PosixPath",
                "pathlib.WindowsPath",
            } and node.func.attr in {"iterdir", "glob", "rglob"}:
                return _Value(
                    "collections.abc.Iterator",
                    instance=True,
                    arguments=(_Value("pathlib.Path", instance=True),),
                )
            if receiver.name == "builtins.str":
                if node.func.attr in {"split", "rsplit", "splitlines"}:
                    return _Value(
                        "builtins.list",
                        instance=True,
                        exact=True,
                        arguments=(_Value("builtins.str", instance=True, exact=True),),
                    )
                if node.func.attr in {
                    "lower",
                    "upper",
                    "strip",
                    "lstrip",
                    "rstrip",
                    "replace",
                    "join",
                    "format",
                    "removeprefix",
                    "removesuffix",
                    "casefold",
                }:
                    return _Value("builtins.str", instance=True, exact=True)
            if (
                receiver.name in {"contextlib.ExitStack", "contextlib.AsyncExitStack"}
                and node.func.attr == "pop_all"
            ):
                return replace(
                    receiver, site=self.site(node, module, env, receiver.name)
                )
            if receiver.name == "builtins.dict" and len(receiver.arguments) == 2:
                mapping_key, mapping_value = receiver.arguments
                if receiver.hint and len(receiver.hint.arguments) == 2:
                    mapping_key = (
                        receiver.hint.arguments[0]
                        if mapping_key.name == "<empty>"
                        else mapping_key
                    )
                    mapping_value = (
                        receiver.hint.arguments[1]
                        if mapping_value.name == "<empty>"
                        else mapping_value
                    )
                if node.func.attr in {"get", "pop"}:
                    return _join(
                        mapping_value,
                        self.infer(node.args[1], module, env, seen)
                        if len(node.args) > 1
                        else _Value("builtins.NoneType", instance=True, exact=True),
                    )
                element = {
                    "keys": mapping_key,
                    "values": mapping_value,
                    "items": _Value(
                        "builtins.tuple",
                        instance=True,
                        exact=True,
                        arguments=(mapping_key, mapping_value),
                    ),
                }.get(node.func.attr)
                if element is not None:
                    return _Value(
                        "collections.abc.Iterable",
                        instance=True,
                        exact=True,
                        arguments=(element,),
                    )
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
                    return _Value(f"{prefix}ScalarResult", instance=True)
                if node.func.attr == "mappings":
                    return _Value(f"{prefix}MappingResult", instance=True)
        if self.call_origin(target):
            signature = self.libraries.returned(target, awaited=awaited)
            if signature is not None:
                resource_name = self.libraries.canonical(signature.name)
                if (
                    resource_name == "pydantic.type_adapter.TypeAdapter"
                    and signature.exact
                    and node.args
                ):
                    signature = replace(
                        signature,
                        fields=(("$schema", self.infer(node.args[0], module, env)),),
                    )
                if (
                    resource_name
                    in {"cdpify.client.CdpClient", "httpx2._client.AsyncClient"}
                    and signature.exact
                ):
                    extensions = {"transport", "auth", "event_hooks", "mounts"}
                    defaults = not any(
                        kw.arg is None
                        or (
                            kw.arg in extensions
                            and not (
                                isinstance(kw.value, ast.Constant)
                                and kw.value.value is None
                            )
                        )
                        for kw in node.keywords
                    )
                    signature = replace(
                        signature,
                        fields=(
                            (
                                "$resource-defaults",
                                _Value(
                                    "builtins.bool",
                                    instance=True,
                                    exact=True,
                                    literal=defaults,
                                ),
                            ),
                        ),
                    )
                return signature
        return _Value()

    def typed(self, node: ast.expr, module: str) -> _Value:
        scope, value = self.value(node, module)
        key = scope, id(value)
        if key in self._typing:
            return _Value()
        self._typing.add(key)
        try:
            return self._typed(value, scope)
        finally:
            self._typing.remove(key)

    def _typed(self, node: ast.expr, module: str) -> _Value:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return _Value()
        module, node = self.value(node, module)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return _join(self.typed(node.left, module), self.typed(node.right, module))
        if isinstance(node, ast.Constant) and node.value is None:
            return _Value("builtins.NoneType", instance=True, exact=True)
        if isinstance(node, ast.Subscript):
            wrapper = self.resolve(node.value, module) or ""
            args = (
                node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            )
            if wrapper in {
                "typing.Annotated",
                "typing_extensions.Annotated",
                "fastapi_canon.Inject",
                "fastapi_canon.injection.Inject",
                "dishka.FromDishka",
                "dishka.integrations.fastapi.FromDishka",
                "fastapi_canon.integrations.websockets.Inject",
            }:
                result = self.typed(args[0], module)
                if wrapper not in {"typing.Annotated", "typing_extensions.Annotated"}:
                    return _Value(
                        result.name,
                        instance=True,
                        dependency=True,
                        arguments=result.arguments,
                        alternatives=result.alternatives,
                    )
                return result
            if wrapper in {"typing.Union", "typing.Optional"}:
                values = [self.typed(arg, module) for arg in args]
                if wrapper == "typing.Optional":
                    values.append(
                        _Value("builtins.NoneType", instance=True, exact=True)
                    )
                return _join(*values)
            if wrapper in {"typing.Literal", "typing_extensions.Literal"}:
                return _join(*(self.infer(arg, module, {}) for arg in args))
            if wrapper in {"typing.Callable", "collections.abc.Callable"}:
                return _Value(
                    "<callable>",
                    instance=True,
                    arguments=(self.typed(args[-1], module),),
                )
            aliases = {
                "typing.List": "builtins.list",
                "typing.Dict": "builtins.dict",
                "typing.Set": "builtins.set",
                "typing.Tuple": "builtins.tuple",
            }
            wrapper = aliases.get(wrapper, wrapper)
            if (
                wrapper
                in {
                    "builtins.list",
                    "builtins.dict",
                    "builtins.set",
                    "builtins.tuple",
                    "builtins.frozenset",
                }
                or wrapper in _ITERATORS
                or wrapper in _RESULT_TYPES
                or wrapper
                in {
                    "collections.abc.Sequence",
                    "collections.abc.Iterable",
                    "typing.Sequence",
                    "typing.Iterable",
                    "tempfile.TemporaryDirectory",
                    "asyncio.Task",
                    "asyncio.Future",
                    "asyncio.Queue",
                    "asyncio.queues.Queue",
                    "collections.abc.Mapping",
                    "typing.Mapping",
                }
            ):
                return _Value(
                    wrapper,
                    instance=True,
                    arguments=tuple(
                        self.typed(arg, module)
                        for arg in args
                        if not (isinstance(arg, ast.Constant) and arg.value is Ellipsis)
                    ),
                )
            return _Value()
        name = self.annotation(node, module) or ""
        if name in {"pydantic.JsonValue", "pydantic.types.JsonValue"}:
            return self.models.json("annotation:pydantic.JsonValue")
        return _Value(name, instance=True)

    def member(self, receiver: _Value, attribute: str) -> _Value:
        reference = receiver.reference()
        receiver = self.object(receiver)
        if receiver.alternatives:
            return _join(*(self.member(v, attribute) for v in receiver.variants))
        if receiver.name in {
            "<empty>",
            "builtins.NoneType",
        } and not attribute.startswith("__"):
            return _Value("<empty>")
        if receiver.name == "<super>" and receiver.bound:
            order = self.mro(receiver.bound.name)
            if not order or receiver.receiver not in order:
                return _Value()
            for base_owner in order[order.index(receiver.receiver) + 1 :]:
                method = self.definitions.get(f"{base_owner}.{attribute}")
                if method:
                    return _Value(
                        method.name, receiver=receiver.bound.name, bound=receiver.bound
                    )
                if self.external(base_owner):
                    return _Value(
                        f"{base_owner}.{attribute}",
                        receiver=receiver.bound.name,
                        bound=receiver.bound,
                    )
            return _Value()
        if receiver.unresolved:
            return _Value(
                f"{receiver.name}.{attribute}",
                bound=reference,
                receiver=receiver.name,
                unresolved=True,
            )
        fields = dict(receiver.fields)
        if attribute in fields:
            tasks = self.task_field(reference, attribute)
            if tasks is not None:
                return _join(fields[attribute], tasks)
            declared = self.field_type(receiver.name, attribute, mutable=True)
            return _join(fields[attribute], declared) if declared else fields[attribute]
        owner = self.definitions.get(receiver.name)
        if receiver.instance and not receiver.exact and not receiver.dependency:
            candidates = [receiver.name, *self.subclasses(receiver.name)]
            return _join(
                *(
                    self.member(replace(reference, name=name, exact=True), attribute)
                    for name in candidates
                )
            )
        if owner and isinstance(owner.node, ast.ClassDef):
            if self.model(receiver.name) and attribute == "model_extra":
                extras: list[ast.expr] = []
                for cls in (self.lineage(receiver.name) or ([receiver.name], False))[0]:
                    definition = self.definitions[cls]
                    for declaration in definition.node.body:
                        if isinstance(declaration, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == "model_config"
                            for t in declaration.targets
                        ):
                            config = declaration.value
                            if isinstance(config, ast.Call):
                                extras.extend(
                                    kw.value
                                    for kw in config.keywords
                                    if kw.arg == "extra"
                                )
                            elif isinstance(config, ast.Dict):
                                extras.extend(
                                    v
                                    for k, v in zip(
                                        config.keys, config.values, strict=True
                                    )
                                    if isinstance(k, ast.Constant)
                                    and k.value == "extra"
                                )
                                if any(k is None for k in config.keys):
                                    extras.append(ast.Name(id="$unknown"))
                            else:
                                extras.append(ast.Name(id="$unknown"))
                if not extras or all(
                    isinstance(v, ast.Constant) and v.value in {"forbid", "ignore"}
                    for v in extras
                ):
                    return _Value("builtins.NoneType", instance=True, exact=True)
            method = self.method(receiver.name, attribute)
            if method:
                if receiver.instance and any(
                    self.resolve(d, method.module)
                    in {"builtins.property", "functools.cached_property"}
                    for d in method.node.decorator_list
                ):
                    assert isinstance(
                        method.node, ast.FunctionDef | ast.AsyncFunctionDef
                    )
                    positional = [*method.node.args.posonlyargs, *method.node.args.args]
                    bindings = {positional[0].arg: reference} if positional else {}
                    returned = self.returned(method, bindings)
                    return (
                        returned
                        if returned.name or returned.alternatives
                        else self.typed(method.node.returns, method.module)
                        if method.node.returns
                        else _Value()
                    )
                if self.classmethod(method):
                    bound = _Value(receiver.name, exact=receiver.exact)
                elif any(
                    self.resolve(d, method.module) == "builtins.staticmethod"
                    for d in method.node.decorator_list
                ):
                    bound = None
                else:
                    bound = reference if receiver.instance else None
                return _Value(method.name, receiver=receiver.name, bound=bound)
            lineage = self.lineage(receiver.name)
            for name in lineage[0] if lineage else [receiver.name]:
                definition = self.definitions[name]
                assert isinstance(definition.node, ast.ClassDef)
                for node in definition.node.body:
                    if (
                        isinstance(node, ast.AnnAssign)
                        and isinstance(node.target, ast.Name)
                        and node.target.id == attribute
                    ):
                        return self.typed(node.annotation, definition.module)
            for base in self.external_bases(receiver.name):
                if self.model(receiver.name) and attribute == "model_fields":
                    return self.model_metadata(receiver.name)
                if self.model(receiver.name) and attribute == "model_fields_set":
                    return _Value(
                        "builtins.set",
                        instance=True,
                        arguments=(_Value("builtins.str", instance=True),),
                    )
                result = self.libraries.member(replace(receiver, name=base), attribute)
                if result is not None:
                    return (
                        replace(result, bound=reference, receiver=reference.name)
                        if result.bound
                        else result
                    )
            return _Value()
        if not receiver.name or receiver.name.startswith("<"):
            return _Value()
        if receiver.name in {
            "pathlib.Path",
            "pathlib.PosixPath",
            "pathlib.WindowsPath",
        }:
            if attribute == "parent":
                return _Value("pathlib.Path", instance=True)
            if attribute == "parents":
                return _Value(
                    "collections.abc.Sequence",
                    instance=True,
                    arguments=(_Value("pathlib.Path", instance=True),),
                )
            if attribute in {"name", "stem", "suffix"}:
                return _Value("builtins.str", instance=True, exact=True)
        if self.external(receiver.name):
            result = self.libraries.member(receiver, attribute)
            if result is not None:
                return result
            if (
                not receiver.instance
                and receiver.name not in self.module_names
                and self.libraries.definition(receiver.name) is None
                and self.libraries.module(receiver.name) is None
                and not receiver.name.startswith("builtins.")
            ):
                return _Value()
        return _Value(
            self.canonical(f"{receiver.name}.{attribute}"),
            receiver=receiver.name,
            bound=receiver if receiver.instance else None,
        )

    def external_bases(
        self, owner: str, seen: frozenset[str] = frozenset()
    ) -> tuple[str, ...]:
        if owner in seen:
            return ()
        definition = self.definitions.get(owner)
        if not definition or not isinstance(definition.node, ast.ClassDef):
            return ()
        result: list[str] = []
        for node in definition.node.bases:
            base = self.resolve(
                node.value if isinstance(node, ast.Subscript) else node,
                definition.module,
            )
            if base in self.definitions:
                result.extend(self.external_bases(base, seen | {owner}))
            elif base and self.external(base):
                result.append(base)
        return tuple(dict.fromkeys(result))

    def model(self, name: str) -> bool:
        return any(
            self.libraries.canonical(base) == "pydantic.main.BaseModel"
            for base in (self.mro(name) or self.external_bases(name))
        )

    def field_type(
        self, owner: str, attribute: str, *, mutable: bool = False
    ) -> _Value | None:
        for name in (self.lineage(owner) or ([owner], False))[0]:
            definition = self.definitions.get(name)
            if not definition or not isinstance(definition.node, ast.ClassDef):
                continue
            annotation = None
            written = False
            for statement in definition.node.body:
                if (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id == attribute
                ):
                    annotation = statement.annotation
                if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                    positional = [*statement.args.posonlyargs, *statement.args.args]
                    if not positional:
                        continue
                    for child in ast.walk(statement):
                        if (
                            isinstance(child, ast.Attribute)
                            and isinstance(child.ctx, ast.Store | ast.Del)
                            and isinstance(child.value, ast.Name)
                            and child.value.id == positional[0].arg
                            and child.attr == attribute
                            and statement.name != "__init__"
                        ):
                            written = True
                        if (
                            isinstance(child, ast.AnnAssign)
                            and isinstance(child.target, ast.Attribute)
                            and isinstance(child.target.value, ast.Name)
                            and child.target.value.id == positional[0].arg
                            and child.target.attr == attribute
                        ):
                            annotation = child.annotation
            if annotation is not None and (not mutable or written):
                return self.typed(annotation, definition.module)
        return None

    def model_metadata(self, owner: str) -> _Value:
        fields = {}
        for name in reversed((self.lineage(owner) or ([owner], False))[0]):
            definition = self.definitions[name]
            assert isinstance(definition.node, ast.ClassDef)
            for node in definition.node.body:
                if not isinstance(node, ast.AnnAssign) or not isinstance(
                    node.target, ast.Name
                ):
                    continue
                if isinstance(node.annotation, ast.Subscript) and self.resolve(
                    node.annotation.value, definition.module
                ) in {"typing.ClassVar", "typing_extensions.ClassVar"}:
                    continue
                alias = (
                    _keyword(node.value, "alias")
                    if isinstance(node.value, ast.Call)
                    else None
                )
                serialization_alias = (
                    _keyword(node.value, "serialization_alias")
                    if isinstance(node.value, ast.Call)
                    else None
                )
                none = _Value("builtins.NoneType", instance=True, exact=True)
                fields[node.target.id] = _Value(
                    "pydantic.fields.FieldInfo",
                    instance=True,
                    fields=(
                        (
                            "alias",
                            self.infer(alias, definition.module, {}) if alias else none,
                        ),
                        (
                            "serialization_alias",
                            self.infer(serialization_alias, definition.module, {})
                            if serialization_alias
                            else none,
                        ),
                    ),
                )
        keys = _join(
            *(
                _Value("builtins.str", instance=True, exact=True, literal=name)
                for name in fields
            )
        )
        return _Value(
            "builtins.dict",
            instance=True,
            exact=True,
            keys_known=True,
            arguments=(keys, _join(*fields.values())),
            fields=tuple(fields.items()),
        )

    def coroutine(self, value: _Value) -> _Value | None:
        if value.name == "<coroutine>":
            return value
        if (
            value.name in {"asyncio.Task", "asyncio.tasks.Task", "_asyncio.Task"}
            and value.arguments
            and value.arguments[0].name == "<coroutine>"
        ):
            return value.arguments[0]
        return None

    def task_field(self, receiver: _Value, attribute: str) -> _Value | None:
        annotation = self.field_type(receiver.name, attribute, mutable=True)
        if not annotation or not any(
            v.name in {"asyncio.Task", "asyncio.tasks.Task"}
            for v in annotation.variants
        ):
            return None
        key = receiver.name, attribute
        if key in self._task_fields:
            return None
        self._task_fields.add(key)
        try:
            tasks = []
            for owner in (self.lineage(receiver.name) or ([receiver.name], False))[0]:
                cls = self.definitions[owner]
                assert isinstance(cls.node, ast.ClassDef)
                for node in cls.node.body:
                    if (
                        not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                        or node.name == "__init__"
                    ):
                        continue
                    positional = [*node.args.posonlyargs, *node.args.args]
                    if not positional:
                        continue
                    direct = {
                        id(target)
                        for child in ast.walk(node)
                        if isinstance(child, ast.Assign | ast.AnnAssign)
                        for target in (
                            child.targets
                            if isinstance(child, ast.Assign)
                            else [child.target]
                        )
                    }
                    if any(
                        isinstance(child, ast.Attribute)
                        and isinstance(child.ctx, ast.Store | ast.Del)
                        and child.attr == attribute
                        and isinstance(child.value, ast.Name)
                        and child.value.id == positional[0].arg
                        and id(child) not in direct
                        for child in ast.walk(node)
                    ):
                        return None
                    for child in ast.walk(node):
                        if not isinstance(child, ast.Assign | ast.AnnAssign):
                            continue
                        targets = (
                            child.targets
                            if isinstance(child, ast.Assign)
                            else [child.target]
                        )
                        if not any(
                            isinstance(t, ast.Attribute)
                            and t.attr == attribute
                            and isinstance(t.value, ast.Name)
                            and t.value.id == positional[0].arg
                            for t in targets
                        ):
                            continue
                        if (
                            isinstance(child.value, ast.Constant)
                            and child.value.value is None
                        ):
                            continue
                        if not isinstance(child.value, ast.Call) or self.resolve(
                            child.value.func, cls.module
                        ) not in {"asyncio.create_task", "asyncio.tasks.create_task"}:
                            return None
                        definition = self.definitions[f"{owner}.{node.name}"]
                        local = self.environment(
                            definition,
                            receiver.name,
                            bindings={positional[0].arg: receiver},
                        )
                        task = self.infer(child.value, cls.module, local)
                        if self.coroutine(task) is None:
                            return None
                        tasks.append(task)
            return _join(*tasks) if tasks else None
        finally:
            self._task_fields.remove(key)

    def call_environment(
        self,
        function: _Definition,
        call: ast.Call,
        module: str,
        env: _Environment,
        bound: _Value | None = None,
        *,
        captures: tuple[tuple[str, _Value], ...] = (),
    ) -> _Environment | None:
        assert isinstance(function.node, ast.FunctionDef | ast.AsyncFunctionDef)
        positional: list[_Value] = []
        for argument in call.args:
            value = self.infer(
                argument.value if isinstance(argument, ast.Starred) else argument,
                module,
                env,
            )
            if isinstance(argument, ast.Starred):
                if value.name != "builtins.tuple" or value.variadic:
                    return None
                positional.extend(value.arguments)
            else:
                positional.append(value)
        keywords: list[tuple[str, _Value]] = []
        for keyword in call.keywords:
            value = self.object(self.infer(keyword.value, module, env))
            if keyword.arg is None:
                if value.name != "builtins.dict" or not value.keys_known:
                    return None
                keywords.extend(value.fields)
            else:
                keywords.append((keyword.arg, value))
        if bound is not None:
            positional.insert(0, bound)
        captured = dict(captures)
        defaults = tuple(
            captured[f"$default:{index}"]
            if f"$default:{index}" in captured
            else self.infer(value, function.module, captured)
            for index, value in enumerate(function.node.args.defaults)
        )
        kw_defaults = tuple(
            captured[f"$kwdefault:{index}"]
            if f"$kwdefault:{index}" in captured
            else self.infer(value, function.module, captured)
            if value is not None
            else None
            for index, value in enumerate(function.node.args.kw_defaults)
        )
        result = _bind(
            function.node.args,
            tuple(positional),
            tuple(keywords),
            defaults,
            kw_defaults,
        )
        return {**dict(captures), **result} if result is not None else None

    def construct(
        self,
        owner: str,
        call: ast.Call,
        module: str,
        env: _Environment,
        *,
        injected: bool = False,
    ) -> _Value:
        definition = self.definitions.get(owner)
        if not definition or not isinstance(definition.node, ast.ClassDef):
            return _Value()
        site = self.site(call, module, env, owner)
        instance = _Value(owner, instance=True, exact=True, site=site)
        if site not in self.heap:
            self.store(instance)
        constructor = self.method(owner, "__init__")
        fields: _Environment = {}
        if constructor and isinstance(constructor.node, ast.FunctionDef):
            bound = self.call_environment(constructor, call, module, env, instance)
            if bound is None:
                return _Value(owner, instance=True)
            key = owner, tuple(sorted(bound.items()))
            if key in self._constructing:
                return _Value(owner, instance=True)
            self._constructing.add(key)
            try:
                local = dict(bound)
                local["$context"] = _Value(
                    constructor.name, fields=tuple(sorted(bound.items()))
                )
                local["$module"] = _Value(constructor.module)
                local["$owner"] = _Value(constructor.owner or owner)
                local["$self"] = instance
                self.values_block(constructor.node.body, constructor.module, local)
                fields = {
                    **dict(self.object(instance).fields),
                    **{
                        name.removeprefix("self."): value
                        for name, value in local.items()
                        if name.startswith("self.")
                    },
                }
            finally:
                self._constructing.remove(key)
        else:
            declared: list[tuple[ast.AnnAssign, str]] = []
            for base in reversed((self.lineage(owner) or ([owner], False))[0]):
                base_definition = self.definitions[base]
                assert isinstance(base_definition.node, ast.ClassDef)
                declared.extend(
                    (node, base_definition.module)
                    for node in base_definition.node.body
                    if isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                )
            positional = list(call.args)
            keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            for index, (node, scope) in enumerate(declared):
                assert isinstance(node.target, ast.Name)
                actual = (
                    positional[index]
                    if index < len(positional)
                    else keywords.get(node.target.id)
                )
                if actual is not None:
                    fields[node.target.id] = self.infer(actual, module, env)
                    if self.model(owner):
                        fields[node.target.id] = self.models.normalize(
                            self.typed(node.annotation, scope),
                            fields[node.target.id],
                            site + ":" + node.target.id,
                        )
                elif node.value is not None and (
                    isinstance(node.value, ast.Call)
                    and self.resolve(node.value.func, scope)
                    in {"dataclasses.field", "pydantic.Field"}
                ):
                    factory = _keyword(node.value, "default_factory")
                    default = _keyword(node.value, "default")
                    if factory:
                        invocation = ast.copy_location(
                            ast.Call(func=factory, args=[], keywords=[]), node.value
                        )
                        fields[node.target.id] = self.infer(invocation, scope, {})
                    elif default is not None:
                        fields[node.target.id] = self.infer(default, scope, {})
                    else:
                        fields[node.target.id] = self.typed(node.annotation, scope)
                elif node.value is not None:
                    fields[node.target.id] = self.infer(node.value, scope, {})
                else:
                    fields[node.target.id] = self.typed(node.annotation, scope)
        result = self.store(
            _Value(
                owner,
                instance=True,
                exact=True,
                initialized=True,
                fields=tuple(sorted(fields.items())),
                site=site,
            )
        )
        if self.model(owner):
            self.models.after(result)
        return result

    def returned(
        self,
        function: _Definition,
        bindings: _Environment | None,
        *,
        yielded: bool = False,
    ) -> _Value:
        if bindings is None:
            return _Value()
        variants = _contexts(
            {name: self.expand(value) for name, value in bindings.items()}
        )
        if any(self.expand(value).name == "<empty>" for value in bindings.values()):
            return _Value("<empty>")
        if variants is None:
            return _Value()
        if len(variants) > 1:
            return _join(
                *(
                    self.returned(function, variant, yielded=yielded)
                    for variant in variants
                )
            )
        bindings = variants[0]
        key = (
            function.name + (":yield" if yielded else ""),
            tuple(
                sorted(
                    (name, self.expand(value).reference())
                    for name, value in bindings.items()
                )
            ),
        )
        if key not in self._return_sites:
            site = f"<return>:{function.name}@{len(self._return_sites)}"
            self._return_sites[key] = site
            self.heap[site] = _Value("<empty>")
            self.revision += 1
        site = self._return_sites[key]
        reference = _Value("<return>", instance=True, site=site)
        self.observe(site)
        if key in self._inferring:
            return reference
        cached = self._return_summaries.get(key)
        if cached and all(
            self.heap.get(name) == version for name, version in cached.reads
        ):
            self.touched.update(cached.modules)
            for name, _ in cached.reads:
                self.observe(name)
            return reference
        if len(self._inferring) >= 64:
            return _Value()
        assert isinstance(function.node, ast.FunctionDef | ast.AsyncFunctionDef)
        previous_modules, previous_reads = self.touched, self.reads
        previous_versions, previous_unstable = self.read_versions, self.unstable_reads
        self.touched = set()
        self.reads = set()
        self.read_versions = {}
        self.unstable_reads = set()
        self._inferring.add(key)
        try:
            local = self.environment(function, bindings=bindings)
            if yielded:
                phases = _phases(function.node.body)
                if phases is None:
                    values = [_Value()]
                else:
                    self.values_block(phases.before, function.module, local)
                    values = [
                        self.infer(phases.yielded, function.module, local)
                        if phases.yielded
                        else _Value("builtins.NoneType", instance=True)
                    ]
            else:
                values = self.values_block(function.node.body, function.module, local)
            pending: list[ast.AST] = list(function.node.body)
            yielding = False
            while pending:
                child = pending.pop()
                if isinstance(
                    child,
                    ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda,
                ):
                    continue
                if isinstance(child, ast.Yield | ast.YieldFrom):
                    yielding = True
                    break
                pending.extend(ast.iter_child_nodes(child))
            if not yielding and "$terminated" not in local:
                values.append(_Value("builtins.NoneType", instance=True, exact=True))
            result = (
                _join(self.heap[site], *(value.reference() for value in values))
                if values
                else self.heap[site]
            )
            if self.heap[site] != result:
                self.heap[site] = result
                self.revision += 1
            if not self.unstable_reads and all(
                self.heap.get(name) == version
                for name, version in self.read_versions.items()
            ):
                self._return_summaries[key] = _ValueSummary(
                    frozenset(self.touched), tuple(sorted(self.read_versions.items()))
                )
            return reference
        finally:
            self._inferring.remove(key)
            previous_modules.update(self.touched)
            previous_reads.update(self.reads)
            for name, version in self.read_versions.items():
                if name in previous_versions and previous_versions[name] != version:
                    previous_unstable.add(name)
                previous_versions.setdefault(name, version)
            previous_unstable.update(self.unstable_reads)
            self.touched = previous_modules
            self.reads = previous_reads
            self.read_versions = previous_versions
            self.unstable_reads = previous_unstable

    def values_block(
        self,
        statements: list[ast.stmt],
        module: str,
        env: _Environment,
        states: list[_Environment] | None = None,
    ) -> list[_Value]:
        returned = []
        for node in statements:
            if states is not None:
                states.append(
                    {key: value for key, value in env.items() if key != "$terminated"}
                )
            match node:
                case ast.Assign() | ast.AnnAssign():
                    if isinstance(node, ast.AnnAssign) and node.value is None:
                        continue
                    if node.value is not None:
                        value = self.infer(node.value, module, env)
                    else:
                        assert isinstance(node, ast.AnnAssign)
                        value = self.typed(node.annotation, module)
                    if isinstance(node, ast.AnnAssign):
                        actual = self.object(value)
                        if actual.site:
                            self.store(
                                replace(
                                    actual, hint=self.typed(node.annotation, module)
                                )
                            )
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    ):
                        self.assign(target, value, env)
                case ast.Return():
                    returned.append(
                        self.infer(node.value, module, env)
                        if node.value
                        else _Value("builtins.NoneType", instance=True, exact=True)
                    )
                    env["$terminated"] = _Value("return")
                    break
                case ast.Expr() if isinstance(node.value, ast.Yield):
                    returned.append(
                        self.infer(node.value.value, module, env)
                        if node.value.value
                        else _Value("builtins.NoneType", instance=True)
                    )
                case ast.Expr():
                    self.infer(node.value, module, env)
                case ast.If():
                    for expression in ast.walk(node.test):
                        if isinstance(expression, ast.NamedExpr):
                            self.assign(expression.target, _Value(), env)
                    left, right = (
                        self.narrow(node.test, module, env, True),
                        self.narrow(node.test, module, env, False),
                    )
                    condition = self.condition(node.test, module, env)
                    if condition is not False:
                        returned.extend(
                            self.values_block(node.body, module, left, states)
                        )
                    if condition is not True:
                        returned.extend(
                            self.values_block(node.orelse, module, right, states)
                        )
                    continuing = []
                    if condition is not False and "$terminated" not in left:
                        continuing.append(left)
                    if condition is not True and "$terminated" not in right:
                        continuing.append(right)
                    if continuing:
                        self.merge_environments(env, continuing)
                    if not continuing:
                        env["$terminated"] = _Value("terminated")
                        break
                case ast.Raise():
                    env["$terminated"] = _Value("raise")
                    break
                case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
                    env[node.name] = (
                        self.local_definition(node, module, env)
                        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                        else _Value()
                    )
                case ast.Try():
                    success = env.copy()
                    exception_states: list[_Environment] = []
                    returned.extend(
                        self.values_block(node.body, module, success, exception_states)
                    )
                    if states is not None:
                        states.extend(exception_states)
                    handler_entry = env.copy()
                    self.merge_environments(handler_entry, exception_states)
                    continuing = []
                    if "$terminated" not in success:
                        returned.extend(
                            self.values_block(node.orelse, module, success, states)
                        )
                        if "$terminated" not in success:
                            continuing.append(success)
                    for handler in node.handlers:
                        local = handler_entry.copy()
                        returned.extend(
                            self.values_block(handler.body, module, local, states)
                        )
                        if "$terminated" not in local:
                            continuing.append(local)
                    self.merge_environments(env, continuing or [handler_entry])
                    env.pop("$terminated", None)
                    returned.extend(
                        self.values_block(node.finalbody, module, env, states)
                    )
                    if not continuing or "$terminated" in env:
                        env["$terminated"] = _Value("terminated")
                        break
                case ast.With() | ast.AsyncWith():
                    for item in node.items:
                        if item.optional_vars:
                            manager = self.infer(item.context_expr, module, env)
                            entered = []
                            for variant in manager.variants:
                                method = self.member(
                                    variant,
                                    "__aenter__"
                                    if isinstance(node, ast.AsyncWith)
                                    else "__enter__",
                                )
                                entered_return = self.libraries.returned(
                                    method, awaited=isinstance(node, ast.AsyncWith)
                                )
                                entered.append(entered_return or _Value())
                            self.assign(item.optional_vars, _join(*entered), env)
                    returned.extend(self.values_block(node.body, module, env, states))
                case ast.For() | ast.AsyncFor() | ast.While():
                    local = env.copy()
                    if isinstance(node, ast.For | ast.AsyncFor):
                        self.assign(
                            node.target,
                            self.element(self.infer(node.iter, module, env)),
                            local,
                        )
                    returned.extend(self.values_block(node.body, module, local, states))
                    self.merge_environments(env, [env.copy(), local])
                    returned.extend(self.values_block(node.orelse, module, env, states))
                case ast.AugAssign():
                    value = self.infer(node.target, module, env)
                    self.assign(
                        node.target,
                        _join(
                            *(
                                replace(variant, literal=None)
                                if variant.name
                                in {
                                    "builtins.int",
                                    "builtins.float",
                                    "builtins.str",
                                    "builtins.bytes",
                                }
                                else _Value()
                                for variant in value.variants
                            )
                        ),
                        env,
                    )
                case ast.Delete():
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self.assign(target, _Value(), env)
                case ast.Match() | ast.TryStar():
                    if any(
                        isinstance(child, ast.Return | ast.Yield | ast.YieldFrom)
                        for child in ast.walk(node)
                    ):
                        returned.append(_Value())
                    for expression in ast.walk(node):
                        if isinstance(
                            expression, ast.Name | ast.Attribute | ast.Subscript
                        ) and isinstance(expression.ctx, ast.Store | ast.Del):
                            self.assign(expression, _Value(), env)
        return returned

    def terminates(
        self, statements: list[ast.stmt], module: str, env: _Environment
    ) -> bool:
        for node in statements:
            if isinstance(node, ast.Return | ast.Raise):
                return True
            if isinstance(node, ast.If):
                condition = self.condition(node.test, module, env)
                if condition is True and self.terminates(node.body, module, env):
                    return True
                if condition is False and self.terminates(node.orelse, module, env):
                    return True
                if (
                    condition is None
                    and self.terminates(node.body, module, env)
                    and self.terminates(node.orelse, module, env)
                ):
                    return True
        return False

    def assign(self, target: ast.expr, value: _Value, env: _Environment) -> None:
        env.pop(f"$narrow:{self.key(target)}", None)
        match target:
            case ast.Tuple() | ast.List():
                for index, node in enumerate(target.elts):
                    self.assign(
                        node,
                        _join(
                            *(
                                actual.arguments[index]
                                if actual.name == "builtins.tuple"
                                and len(actual.arguments) == len(target.elts)
                                else _Value()
                                for variant in value.variants
                                for actual in (self.object(variant),)
                            )
                        ),
                        env,
                    )
            case ast.Name() | ast.Attribute():
                env[self.key(target)] = value
                if isinstance(target, ast.Attribute):
                    receiver = self.infer(
                        target.value, env.get("$module", _Value()).name, env
                    )
                    for variant in receiver.variants:
                        actual = self.object(variant)
                        if actual.site:
                            fields = dict(actual.fields)
                            fields[target.attr] = value
                            self.store(
                                replace(actual, fields=tuple(sorted(fields.items())))
                            )
            case ast.Subscript():
                container = self.object(
                    self.infer(target.value, env.get("$module", _Value()).name, env)
                )
                if container.site and container.name == "builtins.dict":
                    key = self.infer(
                        target.slice, env.get("$module", _Value()).name, env
                    )
                    fields = dict(container.fields)
                    if isinstance(key.literal, str):
                        fields[key.literal] = _join(
                            fields.get(key.literal, _Value("<empty>")), value
                        )
                    else:
                        fields = {
                            name: _join(previous, value)
                            for name, previous in fields.items()
                        }
                    self.store(
                        replace(
                            container,
                            arguments=(key, value),
                            fields=tuple(sorted(fields.items())),
                            keys_known=container.keys_known
                            and isinstance(key.literal, str),
                        )
                    )

    def provided(self, port: str, *, implicit: bool = False) -> _Value:
        self.implementations(port)
        if port in self._providing:
            return _Value()
        plans = self._provider_factories.get(port, [])
        if not plans:
            if self.external(port):
                return _Value(port, instance=True)
            definition = self.definitions.get(port)
            if (
                not implicit
                or not definition
                or not isinstance(definition.node, ast.ClassDef)
                or any(
                    definition.name.startswith(port + ".") and self.abstract(definition)
                    for definition in self.definitions.values()
                    if definition.owner == port
                )
            ):
                return _Value(port, instance=True, dependency=True, unresolved=True)
            plans = [port]
        self._providing.add(port)
        try:
            values = []
            creations = []
            for name in plans:
                if isinstance(name, _ForwardedDependency):
                    values.append(self.provided(name.name))
                    creations.append(_Creation("", dependencies=(name.name,)))
                    continue
                definition = self.definitions.get(name)
                if definition is None:
                    values.append(self.provided(name))
                    creations.append(_Creation("", dependencies=(name,)))
                    continue
                dependencies = []
                if isinstance(definition.node, ast.ClassDef):
                    constructor = self.method(name, "__init__")
                    kwargs = []
                    arguments = {}
                    if constructor and isinstance(constructor.node, ast.FunctionDef):
                        args = [
                            *constructor.node.args.posonlyargs,
                            *constructor.node.args.args,
                            *constructor.node.args.kwonlyargs,
                        ][1:]
                        for arg in args:
                            if arg.annotation:
                                dependency = self.annotation(
                                    arg.annotation, constructor.module
                                )
                                if dependency:
                                    dependencies.append(dependency)
                                    arguments[arg.arg] = self.provided(dependency)
                                    kwargs.append(
                                        ast.keyword(
                                            arg=arg.arg, value=ast.Name(id=arg.arg)
                                        )
                                    )
                    call = ast.copy_location(
                        ast.Call(
                            func=ast.Name(id=definition.node.name),
                            args=[],
                            keywords=kwargs,
                        ),
                        definition.node,
                    )
                    instance = self.construct(
                        name, call, definition.module, arguments, injected=True
                    )
                    values.append(instance)
                    creations.append(
                        _Creation(
                            name,
                            tuple(dependencies),
                            tuple(sorted(arguments.items())),
                            call,
                            instance,
                        )
                    )
                else:
                    arguments = {}
                    parameters = [
                        *definition.node.args.posonlyargs,
                        *definition.node.args.args,
                        *definition.node.args.kwonlyargs,
                    ]
                    for index, arg in enumerate(parameters):
                        if index == 0 and definition.owner:
                            arguments[arg.arg] = _Value(definition.owner, instance=True)
                        elif arg.annotation:
                            forwarded = self._factory_dependency(
                                arg.annotation, definition.module
                            )
                            if forwarded:
                                dependencies.append(forwarded.name)
                            arguments[arg.arg] = (
                                self.provided(forwarded.name)
                                if forwarded
                                else self.typed(arg.annotation, definition.module)
                            )
                        else:
                            arguments[arg.arg] = _Value()
                    values.append(
                        self.returned(
                            definition,
                            arguments,
                            yielded=any(
                                _has_yield(statement)
                                for statement in definition.node.body
                            ),
                        )
                    )
                    creations.append(
                        _Creation(
                            name, tuple(dependencies), tuple(sorted(arguments.items()))
                        )
                    )
            self.creations[port] = creations
            checked = []
            for value in values:
                for variant in self.expand(value).variants:
                    if variant.unresolved or (
                        variant.name
                        and self._provider(variant.name)
                        and not variant.exact
                    ):
                        checked.append(
                            _Value(
                                port, instance=True, dependency=True, unresolved=True
                            )
                        )
                    elif (
                        variant.instance
                        and variant.name
                        and variant.name != "builtins.NoneType"
                    ):
                        checked.append(variant)
                    else:
                        checked.append(
                            _Value(
                                port, instance=True, dependency=True, unresolved=True
                            )
                        )
            return _join(*checked)
        finally:
            self._providing.remove(port)

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

    def call_origin(self, value: _Value) -> str | None:
        return (
            value.name
            if value.name not in _DYNAMIC
            and value.name not in self.definitions
            and self.external(value.name)
            else None
        )

    def origin(
        self,
        node: ast.expr,
        module: str,
        env: _Environment | None = None,
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
            return [item for item in order if item in self.definitions], any(
                item not in self.definitions
                and not item.startswith("builtins.")
                and item not in _MARKER_BASES
                for item in order
            )
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

    def abstract(self, function: _Definition) -> bool:
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
            self.touched = set()
            self._ports = {}
            try:
                for definition in list(self.definitions.values()):
                    if isinstance(definition.node, ast.ClassDef) and self._provider(
                        definition.name
                    ):
                        self._bind(definition.module, definition.node)
            finally:
                self.touched = previous
        if port not in self._ports:
            return ()
        implementations: list[str | None] = []
        pending: list[tuple[str, frozenset[str]]] = [(port, frozenset())]
        while pending:
            dependency, seen = pending.pop()
            bound = self._ports.get(dependency, [])
            for _, modules in bound:
                self.touched.update(modules)
            if dependency in seen or not bound:
                implementations.append(None)
                continue
            for implementation, _ in bound:
                if isinstance(implementation, _ForwardedDependency):
                    pending.append((implementation.name, seen | {dependency}))
                else:
                    implementations.append(implementation)
        return tuple(dict.fromkeys(implementations))

    def subclasses(self, owner: str) -> list[str]:
        """Classes in the source that inherit from *owner*."""
        if owner not in self._subclasses:
            previous = self.touched
            self.touched = set()
            try:
                names = tuple(
                    name
                    for name, definition in self.definitions.items()
                    if name != owner
                    and isinstance(definition.node, ast.ClassDef)
                    and self.derives(name, owner)
                )
                self._subclasses[owner] = names, frozenset(self.touched)
            finally:
                previous.update(self.touched)
                self.touched = previous
        names, modules = self._subclasses[owner]
        self.touched.update(modules)
        return list(names)

    def derives(
        self, name: str, owner: str, seen: frozenset[str] = frozenset()
    ) -> bool:
        if name == owner:
            return True
        if name in seen:
            return False
        definition = self.definitions.get(name)
        if not definition or not isinstance(definition.node, ast.ClassDef):
            return False
        return any(
            self.derives(base, owner, seen | {name})
            for node in definition.node.bases
            if (
                base := self.resolve(
                    node.value if isinstance(node, ast.Subscript) else node,
                    definition.module,
                )
            )
        )

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
            previous = self.touched
            self.touched = {module}
            try:
                port, implementations = self._provider_binding(member, module)
                if port:
                    if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                        plan = f"{module}.{provider.name}.{member.name}"
                        self._provider_factories.setdefault(port, []).append(plan)
                    elif isinstance(member, ast.Assign | ast.AnnAssign) and isinstance(
                        member.value, ast.Call
                    ):
                        source = (
                            member.value.args[0]
                            if member.value.args
                            else _keyword(member.value, "source")
                        )
                        name = self.resolve(source, module) if source else None
                        if name:
                            alias = self.resolve(member.value.func, module) in {
                                "dishka.alias",
                                "dishka.provider.alias",
                            }
                            self._provider_factories.setdefault(port, []).append(
                                _ForwardedDependency(name) if alias else name
                            )
                    modules = frozenset(self.touched)
                    self._ports.setdefault(port, []).extend(
                        (implementation, modules) for implementation in implementations
                    )
            finally:
                self.touched = previous

    def _provider_binding(
        self, member: ast.stmt, module: str
    ) -> tuple[str | None, list[_FactoryValue]]:
        provides: ast.expr | None = None
        implementations: list[_FactoryValue] = []
        if isinstance(member, ast.Assign | ast.AnnAssign) and isinstance(
            member.value, ast.Call
        ):
            call = member.value
            if self.resolve(call.func, module) not in _PROVIDE:
                return None, []
            provides = _keyword(call, "provides")
            source = call.args[0] if call.args else _keyword(call, "source")
            if source is not None:
                implementation = self._class(source, module)
                implementations.append(implementation)
                if provides is None and implementation is not None:
                    provides = source
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
                return None, []
            if isinstance(decorator, ast.Call):
                provides = _keyword(decorator, "provides")
            provides = provides or self._provided(member.returns, module)
            implementations.extend(self._constructed(member, module))
        port = self.annotation(provides, module) if provides else None
        return port, implementations

    def _provided(self, node: ast.expr | None, module: str) -> ast.expr | None:
        # Generator factories annotate Iterator[T] and similar; they provide T.
        if isinstance(node, ast.Subscript) and (
            self.resolve(node.value, module) in _ITERATORS
        ):
            if isinstance(node.slice, ast.Tuple):
                return node.slice.elts[0]
            return node.slice
        return node

    def _constructed(self, factory: _FunctionNode, module: str) -> list[_FactoryValue]:
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
        positional = [*arguments.posonlyargs, *arguments.args]
        bound = (
            positional[:1]
            if not any(
                self.resolve(decorator, module) == "builtins.staticmethod"
                for decorator in factory.decorator_list
            )
            else []
        )
        assigned: _FactoryEnvironment = {
            arg.arg: (
                None
                if arg in bound
                else self._factory_dependency(arg.annotation, module),
            )
            for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        }
        result, remaining = self._factory_block(factory.body, module, assigned)
        if remaining is not None and not yielding:
            result.append(None)
        return result or [None]

    def _factory_dependency(
        self, annotation: ast.expr | None, module: str
    ) -> _ForwardedDependency | None:
        if annotation is None:
            return None
        module, annotation = self.value(annotation, module)
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            try:
                annotation = ast.parse(annotation.value, mode="eval").body
            except SyntaxError:
                return None
        # Only a single source class supplies a dependency key. Optional, union,
        # component-qualified and external annotations remain unresolved.
        name = self._class(annotation, module)
        return _ForwardedDependency(name) if name is not None else None

    def _factory_block(
        self, statements: list[ast.stmt], module: str, assigned: _FactoryEnvironment
    ) -> tuple[list[_FactoryValue], _FactoryEnvironment | None]:
        assigned = dict(assigned)
        result: list[_FactoryValue] = []
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
                case ast.Try(body=body, handlers=[], orelse=[], finalbody=cleanup):
                    # Resource providers yield an implementation before cleanup.
                    if any(
                        isinstance(node, ast.Return | ast.Yield | ast.YieldFrom)
                        for node in ast.walk(ast.Module(body=cleanup, type_ignores=[]))
                    ):
                        return [*result, None], None
                    values, remaining = self._factory_block(body, module, assigned)
                    result.extend(values)
                    _, cleaned = self._factory_block(
                        cleanup, module, remaining or assigned
                    )
                    if remaining is None:
                        return result, None
                    if cleaned is None:
                        return [*result, None], None
                    assigned = cleaned
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
    ) -> tuple[_FactoryValue, ...]:
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
        function: _Definition,
        receiver: str | None = None,
        dependency_transport: str | None = None,
        *,
        bindings: _Environment | None = None,
    ) -> _Environment:
        assert isinstance(function.node, ast.FunctionDef | ast.AsyncFunctionDef)
        scope = self.lexical.get(
            id(function.node), _LexicalScope(frozenset(), frozenset(), frozenset())
        )
        env: _Environment = {name: _Value() for name in scope.local_names}
        env["$locals"] = _Value(
            fields=tuple((name, _Value()) for name in sorted(scope.local_names))
        )
        env["$written"] = _Value(
            fields=tuple((name, _Value()) for name in sorted(scope.assigned_names))
        )
        arguments = function.node.args
        for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
            if bindings is not None and arg.arg in bindings:
                env[arg.arg] = bindings[arg.arg]
                continue
            env[arg.arg] = _Value()
            if arg.annotation is not None:
                value = self.typed(arg.annotation, function.module)
                resolved = value.name
                if resolved or value.alternatives:
                    if resolved in _CONNECTIONS and dependency_transport in {
                        "http",
                        "sse",
                        "webrtc",
                    }:
                        resolved = "starlette.requests.Request"
                        value = _Value(resolved, instance=True)
                    env[arg.arg] = (
                        self.provided(resolved, implicit=True)
                        if value.dependency and resolved
                        else value
                    )
        if bindings is not None:
            env.update(bindings)
        env["$module"] = _Value(function.module)
        env["$context"] = _Value(
            function.name, fields=tuple(sorted((bindings or {}).items()))
        )
        if function.owner:
            owner_type = receiver or function.owner
            positional = [*arguments.posonlyargs, *arguments.args]
            actual = (
                env.get(positional[0].arg)
                if positional and bindings is not None
                else None
            )
            if positional:
                if self.classmethod(function) or function.node.name == "__new__":
                    env[positional[0].arg] = _Value(owner_type)
                elif not any(
                    self.resolve(decorator, function.module) == "builtins.staticmethod"
                    for decorator in function.node.decorator_list
                ):
                    env[positional[0].arg] = _Value(owner_type, instance=True)
            if actual is not None:
                env[positional[0].arg] = actual
                env["$owner"] = _Value(function.owner)
                env["$self"] = actual
                for name, value in self.object(actual).fields:
                    env[f"{positional[0].arg}.{name}"] = value
                if self.object(actual).initialized:
                    return env
            # Base class constructors assign inherited attributes; subclass
            # assignments override them.
            for owner in reversed(self.mro(owner_type) or [owner_type]):
                constructor = self.definitions.get(f"{owner}.__init__")
                if constructor is None or not isinstance(
                    constructor.node, ast.FunctionDef
                ):
                    continue
                constructor_env: _Environment = {
                    "self": _Value(owner_type, instance=True)
                }
                for arg in (
                    constructor.node.args.posonlyargs
                    + constructor.node.args.args
                    + constructor.node.args.kwonlyargs
                ):
                    constructor_env.setdefault(arg.arg, _Value())
                    if arg.annotation:
                        constructor_env[arg.arg] = self.typed(
                            arg.annotation, constructor.module
                        )
                for statement in constructor.node.body:
                    if isinstance(statement, ast.Assign | ast.AnnAssign):
                        inferred = (
                            self.infer(
                                statement.value, constructor.module, constructor_env
                            )
                            if statement.value
                            else _Value()
                        )
                        if isinstance(statement, ast.AnnAssign) and (
                            not inferred.name
                            or (
                                inferred.name
                                in {"builtins.dict", "builtins.list", "builtins.set"}
                                and inferred.element().name == "<empty>"
                            )
                        ):
                            inferred = self.typed(
                                statement.annotation, constructor.module
                            )
                        if inferred.name or inferred.alternatives:
                            for target in (
                                statement.targets
                                if isinstance(statement, ast.Assign)
                                else [statement.target]
                            ):
                                if isinstance(target, ast.Attribute):
                                    env[self.key(target)] = inferred
        return env

    def local_definition(
        self, node: _FunctionNode, module: str, env: _Environment
    ) -> _Value:
        scope = self.lexical.get(id(node))
        if scope is None:
            return _Value()
        captured = scope.free_names
        if (
            node.decorator_list
            or captured & set(dict(env.get("$written", _Value()).fields))
            or any(
                isinstance(child, ast.Nonlocal | ast.Global) for child in ast.walk(node)
            )
        ):
            return _Value()
        name = f"{module}.<closed>.{node.name}:{node.lineno}"
        self.definitions[name] = _Definition(name, module, node)
        return _Value(
            name,
            fields=tuple(
                (name, self.infer(ast.Name(id=name, ctx=ast.Load()), module, env))
                for name in sorted(captured)
            )
            + tuple(
                (f"$default:{index}", self.infer(value, module, env))
                for index, value in enumerate(node.args.defaults)
            )
            + tuple(
                (f"$kwdefault:{index}", self.infer(value, module, env))
                for index, value in enumerate(node.args.kw_defaults)
                if value is not None
            ),
        )

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
