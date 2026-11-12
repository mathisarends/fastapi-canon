"""Read external Python signatures without importing or following library bodies."""

import ast
import builtins
import datetime
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from fastapi_canon.checks.value_flow import _bind, _join, _Value

_PUBLIC_TYPES = {
    "typing.Self": "typing.Self",
    "typing_extensions.Self": "typing.Self",
    "pydantic.BaseModel": "pydantic.main.BaseModel",
    "pydantic.RootModel": "pydantic.root_model.RootModel",
    "pydantic.TypeAdapter": "pydantic.type_adapter.TypeAdapter",
    "pydantic.SecretStr": "pydantic.types.SecretStr",
    "pydantic.SecretBytes": "pydantic.types.SecretBytes",
    "pydantic.BaseSettings": "pydantic_settings.main.BaseSettings",
    "pydantic_settings.BaseSettings": "pydantic_settings.main.BaseSettings",
}
_PUBLIC_TYPES.update(
    {
        f"{module}.{name}": f"collections.abc.{name}"
        for module in ("collections.abc", "_collections_abc")
        for name in ("Mapping", "Sequence", "Iterable", "Iterator", "AsyncIterator")
    }
)
_PUBLIC_TYPES.update(
    {name: "typing.Generic" for name in ("typing.Generic", "_typing.Generic")}
)
_NATIVE_TYPES = {
    f"datetime.{name}": getattr(datetime, name)
    for name in ("date", "datetime", "time", "timedelta", "timezone", "tzinfo")
}
_PUBLIC_TYPES.update(
    {
        f"{module}.{name.rsplit('.', 1)[-1]}": name
        for name in _NATIVE_TYPES
        for module in ("datetime", "_datetime")
    }
)


@dataclass
class _Module:
    aliases: dict[str, str] = field(default_factory=dict)
    definitions: dict[str, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = (
        field(default_factory=dict)
    )
    type_variables: set[str] = field(default_factory=set)
    exports: frozenset[str] | None = None


class _LibraryTypes:
    def __init__(self) -> None:
        self._modules: dict[str, _Module | None] = {}

    def canonical(self, name: str) -> str:
        original = name
        visited = set()
        while True:
            if name in _PUBLIC_TYPES:
                return _PUBLIC_TYPES[name]
            parts = name.split(".")
            for length in range(len(parts) - 1, 0, -1):
                scope = ".".join(parts[:length])
                module = self.module(scope)
                if module is None:
                    continue
                local = parts[length]
                if local in module.definitions or local not in module.aliases:
                    return name
                binding = f"{scope}.{local}"
                if binding in visited:
                    return original
                visited.add(binding)
                name = ".".join([module.aliases[local], *parts[length + 1 :]])
                break
            else:
                return name

    def bases(self, name: str) -> tuple[str, ...] | None:
        definition = self.definition(name)
        if definition is None or not isinstance(definition[1], ast.ClassDef):
            return None
        values = tuple(
            self.expression(
                node.value if isinstance(node, ast.Subscript) else node, definition[0]
            )
            for node in definition[1].bases
        )
        if any(value is None for value in values):
            return None
        return tuple(value for value in values if value) or ("builtins.object",)

    def presence(
        self, name: str, attribute: str, seen: frozenset[str] = frozenset()
    ) -> bool | None:
        name = self.canonical(name)
        if name in seen:
            return None
        if name.startswith("builtins.") or name in _NATIVE_TYPES:
            cls = _NATIVE_TYPES.get(name) or vars(builtins).get(name.rsplit(".", 1)[-1])
            return (
                any(attribute in vars(base) for base in cls.__mro__)
                if isinstance(cls, type)
                else None
            )
        definition = self.definition(name)
        if not definition or not isinstance(definition[1], ast.ClassDef):
            return None
        cls = definition[1]
        if (
            cls.decorator_list
            or cls.keywords
            or any(
                isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                and n.name == "__getattribute__"
                for n in cls.body
            )
        ):
            return None
        for node in cls.body:
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == attribute
            ):
                decorators = {
                    self.expression(d, definition[0]) for d in node.decorator_list
                }
                return (
                    True
                    if decorators <= {"builtins.classmethod", "builtins.staticmethod"}
                    else None
                )
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == attribute
            ):
                return None
        if any(
            isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            and n.name == "__getattr__"
            for n in cls.body
        ):
            return None
        for child in ast.walk(cls):
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.ctx, ast.Store)
                and child.attr == attribute
            ):
                return None
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "__setattr__"
                and len(child.args) >= 2
                and not (
                    isinstance(child.args[1], ast.Constant)
                    and child.args[1].value != attribute
                )
            ):
                return None
        bases = self.bases(name)
        if bases is None:
            return None
        answers = [self.presence(base, attribute, seen | {name}) for base in bases]
        return (
            True
            if True in answers
            else False
            if all(answer is False for answer in answers)
            else None
        )

    def definition(
        self, name: str
    ) -> tuple[str, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] | None:
        name = self.canonical(name)
        scope, _, local = name.rpartition(".")
        module = self.module(scope)
        if module and local in module.definitions:
            return scope, module.definitions[local]
        return None

    def member(
        self, receiver: _Value, attribute: str, seen: frozenset[str] = frozenset()
    ) -> _Value | None:
        name = self.canonical(receiver.name)
        if name in seen:
            return None
        definition = self.definition(name)
        if not definition or not isinstance(definition[1], ast.ClassDef):
            return None
        scope, cls = definition
        assert isinstance(cls, ast.ClassDef)
        for node in cls.body:
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == attribute
            ):
                return self.annotation(node.annotation, scope, receiver)
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == attribute
            ):
                decorators = [self.expression(d, scope) for d in node.decorator_list]
                if any(
                    name
                    in {"builtins.property", "property", "functools.cached_property"}
                    for name in decorators
                ):
                    return (
                        self.annotation(node.returns, scope, receiver)
                        if node.returns
                        else _Value()
                    )
                bound: _Value | None = receiver
                if (
                    "builtins.staticmethod" in decorators
                    or "staticmethod" in decorators
                ):
                    bound = None
                return _Value(
                    f"{name}.{attribute}", receiver=receiver.name, bound=bound
                )
        for base in cls.bases:
            base_name = self.expression(
                base.value if isinstance(base, ast.Subscript) else base, scope
            )
            if base_name:
                value = self.member(
                    replace(receiver, name=base_name), attribute, seen | {name}
                )
                if value is not None:
                    return (
                        replace(value, bound=receiver, receiver=receiver.name)
                        if value.bound
                        else value
                    )
        return None

    def context(
        self,
        target: _Value,
        positional: tuple[_Value, ...],
        keywords: tuple[tuple[str, _Value], ...],
    ) -> _Value | None:
        definition = self.definition(self.canonical(target.name))
        if definition is None or not isinstance(
            definition[1], ast.FunctionDef | ast.AsyncFunctionDef
        ):
            return None
        scope, node = definition
        assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        decorators = {self.expression(item, scope) for item in node.decorator_list}
        asynchronous = isinstance(node, ast.AsyncFunctionDef)
        expected = (
            "contextlib.asynccontextmanager"
            if asynchronous
            else "contextlib.contextmanager"
        )
        if decorators != {expected} or not isinstance(node.returns, ast.Subscript):
            return None
        if (self.expression(node.returns.value, scope) or "") not in {
            "collections.abc.AsyncIterator"
            if asynchronous
            else "collections.abc.Iterator",
            "typing.AsyncIterator" if asynchronous else "typing.Iterator",
        }:
            return None
        actual = _bind(
            node.args,
            positional,
            keywords,
            tuple(_Value() for _ in node.args.defaults),
            tuple(
                _Value() if item is not None else None for item in node.args.kw_defaults
            ),
        )
        element = node.returns.slice
        module = self.module(scope)
        variables = (module.type_variables if module else set()) | {
            parameter.name
            for parameter in node.type_params
            if isinstance(parameter, ast.TypeVar)
        }
        if isinstance(element, ast.Name) and element.id in variables:
            yielded = _join(
                *(
                    actual.get(parameter.arg, _Value())
                    for parameter in (
                        *node.args.posonlyargs,
                        *node.args.args,
                        *node.args.kwonlyargs,
                    )
                    if actual is not None
                    and isinstance(parameter.annotation, ast.Name)
                    and parameter.annotation.id == element.id
                )
            )
        else:
            yielded = self.annotation(element, scope, target.bound)
        return _Value(
            "<external-contextmanager>",
            instance=True,
            bound=yielded,
            fields=(
                (
                    "$async",
                    _Value("builtins.bool", instance=True, literal=asynchronous),
                ),
            ),
            unresolved=actual is None or not yielded.name or yielded.name == "<empty>",
        )

    def returned(self, target: _Value, *, awaited: bool) -> _Value | None:
        name = self.canonical(target.name)
        scope, _, member = name.rpartition(".")
        cls = self.definition(scope)
        if cls and isinstance(cls[1], ast.ClassDef):
            for node in cls[1].body:
                if (
                    isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                    and node.name == member
                ):
                    if isinstance(node, ast.AsyncFunctionDef) != awaited:
                        return None
                    positional = [*node.args.posonlyargs, *node.args.args]
                    module = self.module(cls[0])
                    if (
                        target.bound
                        and module
                        and isinstance(node.returns, ast.Name)
                        and node.returns.id in module.type_variables
                        and positional
                        and isinstance(positional[0].annotation, ast.Name)
                        and positional[0].annotation.id == node.returns.id
                    ):
                        return _Value(
                            target.bound.name,
                            instance=True,
                            arguments=target.bound.arguments,
                            unresolved=target.bound.unresolved,
                        )
                    return (
                        self.annotation(node.returns, cls[0], target.bound)
                        if node.returns
                        else None
                    )
        function = self.definition(name)
        if function:
            if isinstance(function[1], ast.ClassDef):
                return _Value(name, instance=True, exact=True) if not awaited else None
            if (
                isinstance(function[1], ast.AsyncFunctionDef) == awaited
                and function[1].returns
            ):
                return self.annotation(function[1].returns, function[0], target.bound)
        return None

    def annotation(
        self, node: ast.expr | None, scope: str, receiver: _Value | None = None
    ) -> _Value:
        if node is None:
            return _Value()
        if isinstance(node, ast.Constant):
            if node.value is None:
                return _Value("builtins.NoneType", instance=True, exact=True)
            if isinstance(node.value, str):
                try:
                    return self.annotation(
                        ast.parse(node.value, mode="eval").body, scope, receiver
                    )
                except SyntaxError:
                    return _Value()
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return _join(
                self.annotation(node.left, scope, receiver),
                self.annotation(node.right, scope, receiver),
            )
        if isinstance(node, ast.Subscript):
            name = self.expression(node.value, scope)
            args = (
                node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            )
            if name in {"typing.Optional", "typing.Union"}:
                values = [self.annotation(arg, scope, receiver) for arg in args]
                if name == "typing.Optional":
                    values.append(
                        _Value("builtins.NoneType", instance=True, exact=True)
                    )
                return _join(*values)
            aliases = {
                "typing.List": "builtins.list",
                "typing.Dict": "builtins.dict",
                "typing.Tuple": "builtins.tuple",
                "typing.Set": "builtins.set",
            }
            return _Value(
                aliases.get(name or "", name or ""),
                instance=True,
                arguments=tuple(
                    self.annotation(arg, scope, receiver)
                    for arg in args
                    if not (isinstance(arg, ast.Constant) and arg.value is Ellipsis)
                ),
            )
        name = self.expression(node, scope)
        module = self.module(scope)
        if isinstance(node, ast.Name) and module and node.id in module.type_variables:
            return _Value()
        if name in {"typing.Self", "typing_extensions.Self"}:
            return (
                _Value(
                    receiver.name,
                    instance=True,
                    arguments=receiver.arguments,
                    unresolved=receiver.unresolved,
                )
                if receiver
                else _Value()
            )
        if name in {"typing.Any", "typing_extensions.Any", "builtins.object"}:
            return _Value()
        return _Value(name or "", instance=True)

    def expression(self, node: ast.expr, scope: str) -> str | None:
        if isinstance(node, ast.Attribute):
            parent = self.expression(node.value, scope)
            return self.canonical(f"{parent}.{node.attr}") if parent else None
        if isinstance(node, ast.Name):
            module = self.module(scope)
            if not module:
                return None
            if node.id in module.aliases or node.id in module.definitions:
                return self.canonical(module.aliases.get(node.id, f"{scope}.{node.id}"))
            return f"builtins.{node.id}" if node.id in vars(builtins) else None
        return None

    def module(self, name: str) -> _Module | None:
        if name in self._modules:
            return self._modules[name]
        self._modules[name] = None
        parts = name.split(".")
        search = [Path(entry) for entry in sys.path]
        origin = None
        for index, part in enumerate(parts):
            namespaces = []
            origin = None
            for directory in search:
                package_directory = directory / part
                package_file = next(
                    (
                        package_directory / filename
                        for filename in ("__init__.pyi", "__init__.py")
                        if (package_directory / filename).is_file()
                    ),
                    None,
                )
                module_file = next(
                    (
                        directory / f"{part}{suffix}"
                        for suffix in (".pyi", ".py")
                        if (directory / f"{part}{suffix}").is_file()
                    ),
                    None,
                )
                if package_file:
                    origin = package_file
                    search = [package_directory]
                    break
                if module_file:
                    origin = module_file
                    search = []
                    break
                if package_directory.is_dir():
                    namespaces.append(package_directory)
            if origin is None:
                search = namespaces
            if not search and index < len(parts) - 1:
                return None
        if origin is None:
            return None
        try:
            tree = ast.parse(origin.read_text(encoding="utf8"))
        except (OSError, SyntaxError, UnicodeError):
            return None
        result = _Module()
        self._modules[name] = result
        package = (
            name
            if origin.name in {"__init__.py", "__init__.pyi"}
            else name.rpartition(".")[0]
        )
        pending = list(tree.body)
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in statement.targets
            ):
                result.exports = (
                    frozenset(
                        item.value
                        for item in statement.value.elts
                        if isinstance(item, ast.Constant)
                        and isinstance(item.value, str)
                    )
                    if isinstance(statement.value, ast.List | ast.Tuple | ast.Set)
                    and all(
                        isinstance(item, ast.Constant) and isinstance(item.value, str)
                        for item in statement.value.elts
                    )
                    else frozenset()
                )
        for node in pending:
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Constant)
                and isinstance(node.test.value, bool)
            ):
                pending.extend(node.body if node.test.value else node.orelse)
            if isinstance(node, ast.If) and ast.unparse(node.test) in {
                "TYPE_CHECKING",
                "typing.TYPE_CHECKING",
            }:
                pending.extend(node.body)
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and self.expression(node.value.func, name)
                in {"typing.TypeVar", "typing_extensions.TypeVar", "_typing.TypeVar"}
            ):
                result.type_variables.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
            match node:
                case ast.Import():
                    for alias in node.names:
                        result.aliases[alias.asname or alias.name.partition(".")[0]] = (
                            alias.name if alias.asname else alias.name.partition(".")[0]
                        )
                case ast.ImportFrom():
                    parent = (
                        package.split(".")[: len(package.split(".")) - node.level + 1]
                        if node.level
                        else []
                    )
                    target = (
                        ".".join(
                            [*parent, *(node.module.split(".") if node.module else [])]
                        )
                        if node.level
                        else node.module or ""
                    )
                    for alias in node.names:
                        if alias.name != "*":
                            result.aliases[alias.asname or alias.name] = (
                                f"{target}.{alias.name}"
                            )
                        else:
                            imported = self.module(target)
                            if imported:
                                exports = imported.exports
                                if exports is None:
                                    exports = frozenset(
                                        item
                                        for item in imported.definitions.keys()
                                        | imported.aliases.keys()
                                        if not item.startswith("_")
                                    )
                                for exported in exports:
                                    result.aliases[exported] = f"{target}.{exported}"
                case ast.ClassDef() | ast.FunctionDef() | ast.AsyncFunctionDef():
                    result.definitions[node.name] = node
        return result
