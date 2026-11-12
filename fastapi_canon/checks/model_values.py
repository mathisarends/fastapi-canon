"""Abstract values guaranteed by Pydantic's validation and serialization APIs."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import replace
from typing import Protocol

from fastapi_canon.checks.library_types import _LibraryTypes
from fastapi_canon.checks.symbols import _Definition
from fastapi_canon.checks.value_flow import _join, _Value


class _ModelSource(Protocol):
    """Source operations required by model value contracts, independent of indexing."""

    heap: dict[str, _Value]
    revision: int
    libraries: _LibraryTypes

    @property
    def definitions(self) -> Mapping[str, _Definition]: ...

    def lineage(self, name: str) -> tuple[list[str], bool] | None: ...
    def mro(self, name: str) -> tuple[str, ...] | None: ...
    def external_bases(self, owner: str) -> tuple[str, ...]: ...
    def method(self, owner: str, attribute: str) -> _Definition | None: ...
    def value(self, node: ast.expr, module: str) -> tuple[str, ast.expr]: ...
    def resolve(self, node: ast.expr, module: str) -> str | None: ...
    def typed(self, node: ast.expr, module: str) -> _Value: ...
    def model(self, name: str) -> bool: ...
    def object(self, value: _Value) -> _Value: ...
    def expand(self, value: _Value) -> _Value: ...
    def store(self, value: _Value) -> _Value: ...
    def returned(
        self, function: _Definition, bindings: dict[str, _Value] | None
    ) -> _Value: ...


class _Models:
    def __init__(self, source: _ModelSource) -> None:
        self.source = source
        self._validating: set[str] = set()

    def schema(
        self,
        node: ast.expr,
        module: str,
        seen: frozenset[int] = frozenset(),
        *,
        bindings: Mapping[str, _Value] | None = None,
    ) -> _Value:
        """Read supported adapter schemas without losing functional metadata."""
        if isinstance(node, ast.Name) and bindings is not None and node.id in bindings:
            return _join(
                *(
                    replace(value, instance=True)
                    if value.name and not value.instance
                    else _Value(unresolved=True)
                    for value in self.source.expand(bindings[node.id]).variants
                )
            )
        module, node = self.source.value(node, module)
        if id(node) in seen:
            return _Value(unresolved=True)
        seen = seen | {id(node)}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return _Value(unresolved=True)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return _join(
                self.schema(node.left, module, seen, bindings=bindings),
                self.schema(node.right, module, seen, bindings=bindings),
            )
        if isinstance(node, ast.Subscript):
            wrapper = self.source.resolve(node.value, module)
            args = (
                node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            )
            if wrapper in {"typing.Annotated", "typing_extensions.Annotated"}:
                safe_metadata = all(
                    isinstance(value, ast.Call)
                    and self.source.libraries.canonical(
                        self.source.resolve(value.func, module) or ""
                    )
                    == "pydantic.fields.Field"
                    and not any(
                        isinstance(child, ast.Name | ast.Attribute)
                        and self.source.resolve(child, module)
                        in self.source.definitions
                        for child in ast.walk(value)
                    )
                    for value in args[1:]
                )
                return (
                    self.schema(args[0], module, seen, bindings=bindings)
                    if safe_metadata
                    else _Value(unresolved=True)
                )
            if wrapper in {"typing.Union", "typing.Optional"}:
                values = [
                    self.schema(arg, module, seen, bindings=bindings) for arg in args
                ]
                if wrapper == "typing.Optional":
                    values.append(
                        _Value("builtins.NoneType", instance=True, exact=True)
                    )
                return _join(*values)
            value = self.source.typed(node, module)
            return replace(
                value,
                arguments=tuple(
                    self.schema(arg, module, seen, bindings=bindings)
                    for arg in args
                    if not (isinstance(arg, ast.Constant) and arg.value is Ellipsis)
                ),
            )
        if self.source.resolve(node, module) in {"typing.Any", "typing_extensions.Any"}:
            return _Value("<any-schema>")
        return self.source.typed(node, module)

    def supported_schema(self, schema: _Value) -> bool:
        for value in schema.variants:
            if value.unresolved:
                return False
            if self._record(value.name):
                if self._custom_schema(value.name):
                    return False
            elif value.name in {
                "builtins.list",
                "builtins.tuple",
                "builtins.set",
                "builtins.frozenset",
                "builtins.dict",
            }:
                if not value.arguments or not all(
                    self.supported_schema(arg) for arg in value.arguments
                ):
                    return False
            elif value.name not in {
                "<any-schema>",
                "<json>",
                "builtins.str",
                "builtins.bytes",
                "builtins.int",
                "builtins.float",
                "builtins.bool",
                "builtins.NoneType",
            }:
                return False
        return True

    def validated_models(
        self,
        schema: _Value,
        value: _Value,
        seen: frozenset[tuple[str, str]] = frozenset(),
    ) -> list[_Value]:
        """Visit nested validation hooks as well as the adapter's outer model."""
        result = []
        for variant in schema.variants:
            for actual in self.source.expand(value).variants:
                actual = self.source.object(actual)
                key = variant.name, actual.site
                if key in seen:
                    continue
                visited = seen | {key}
                if self._record(variant.name):
                    if actual.name != variant.name:
                        continue
                    result.append(actual)
                    for name, field in self.fields(variant.name):
                        result.extend(
                            self.validated_models(
                                field, dict(actual.fields).get(name, _Value()), visited
                            )
                        )
                elif variant.name in {
                    "builtins.list",
                    "builtins.tuple",
                    "builtins.set",
                    "builtins.frozenset",
                    "builtins.dict",
                }:
                    for index, arg in enumerate(variant.arguments):
                        nested = (
                            actual.arguments[index]
                            if variant.name in {"builtins.dict", "builtins.tuple"}
                            and not variant.variadic
                            and len(actual.arguments) > index
                            else actual.element()
                        )
                        result.extend(self.validated_models(arg, nested, visited))
        return result

    def json(self, site: str) -> _Value:
        """A recursive JSON graph; later writes remain visible through its references."""
        source = self.source
        root = _Value("<json>", instance=True, site=site)
        if site not in source.heap:
            text = _Value("builtins.str", instance=True, exact=True)
            array = _Value(
                "builtins.list", instance=True, exact=True, site=site + ":array"
            )
            mapping = _Value(
                "builtins.dict", instance=True, exact=True, site=site + ":object"
            )
            source.store(replace(array, arguments=(root,)))
            source.store(replace(mapping, arguments=(text, root)))
            source.heap[site] = _join(
                text,
                array,
                mapping,
                *(
                    _Value(f"builtins.{name}", instance=True, exact=True)
                    for name in ("int", "float", "bool", "NoneType")
                ),
            )
            source.revision += 1
        return root

    def settings(self, owner: str) -> bool:
        return any(
            self.source.libraries.canonical(name)
            == "pydantic_settings.main.BaseSettings"
            for name in (self.source.mro(owner) or self.source.external_bases(owner))
        )

    def standard_settings(self, owner: str) -> bool:
        if not self.settings(owner):
            return False
        hooks = {"settings_customise_sources", "_settings_build_values"}
        for name in (self.source.lineage(owner) or ([owner], False))[0]:
            definition = self.source.definitions[name]
            assert isinstance(definition.node, ast.ClassDef)
            for node in definition.node.body:
                if (
                    isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                    and node.name in hooks
                ):
                    return False
                if isinstance(node, ast.Assign | ast.AnnAssign):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    if any(
                        isinstance(target, ast.Name) and target.id in hooks
                        for target in targets
                    ):
                        return False
        return True

    def environment_value(self, annotation: _Value, site: str) -> _Value:
        scalars = {
            "builtins.str",
            "builtins.bytes",
            "builtins.int",
            "builtins.float",
            "builtins.bool",
            "builtins.NoneType",
            "uuid.UUID",
            "pathlib.Path",
            "datetime.datetime",
            "datetime.date",
            "datetime.time",
            "decimal.Decimal",
            "pydantic.SecretStr",
            "pydantic.SecretBytes",
            "pydantic.types.SecretStr",
            "pydantic.types.SecretBytes",
        }
        if all(value.name in scalars for value in annotation.variants):
            return _join(
                _Value("builtins.str", instance=True, exact=True),
                _Value("builtins.NoneType", instance=True, exact=True),
            )
        return self.json(site)

    def fields(self, owner: str) -> list[tuple[str, _Value]]:
        result = {}
        for name in reversed((self.source.lineage(owner) or ([owner], False))[0]):
            definition = self.source.definitions[name]
            assert isinstance(definition.node, ast.ClassDef)
            for node in definition.node.body:
                if (
                    not isinstance(node, ast.AnnAssign)
                    or not isinstance(node.target, ast.Name)
                    or node.target.id.startswith("_")
                ):
                    continue
                if isinstance(node.annotation, ast.Subscript) and self.source.resolve(
                    node.annotation.value, definition.module
                ) in {"typing.ClassVar", "typing_extensions.ClassVar"}:
                    continue
                result[node.target.id] = self.source.typed(
                    node.annotation, definition.module
                )
        return list(result.items())

    def normalize(self, annotation: _Value, value: _Value, site: str) -> _Value:
        if annotation.alternatives:
            return _join(
                *(
                    self.normalize(v, value, site + f":{i}")
                    for i, v in enumerate(annotation.variants)
                )
            )
        if not annotation.name or annotation.name == "<any-schema>":
            return value
        if annotation.name == "<json>":
            return self.json(site)
        if annotation.name in {
            "pydantic.SecretStr",
            "pydantic.types.SecretStr",
            "pydantic.SecretBytes",
            "pydantic.types.SecretBytes",
        }:
            owner = self.source.libraries.canonical(annotation.name)
            results = []
            for secret_input in self.source.expand(value).variants:
                actual = self.source.object(secret_input)
                if self.source.libraries.canonical(actual.name) == owner or any(
                    self.source.libraries.canonical(base) == owner
                    for base in self.source.mro(actual.name) or ()
                ):
                    results.append(actual)
                else:
                    native = actual.name in {
                        "builtins.str",
                        "builtins.bytes",
                        "builtins.int",
                        "builtins.float",
                        "builtins.bool",
                        "builtins.NoneType",
                        "<empty>",
                    }
                    payload = (
                        _Value(
                            "builtins.str"
                            if owner.endswith("SecretStr")
                            else "builtins.bytes",
                            instance=True,
                            exact=True,
                        )
                        if native
                        else _Value()
                    )
                    results.append(
                        self.source.store(
                            _Value(
                                owner,
                                instance=True,
                                exact=True,
                                fields=(("_secret_value", payload),),
                                site=site,
                            )
                        )
                    )
            return _join(*results)
        if (
            annotation.name
            in {
                "builtins.str",
                "builtins.bytes",
                "builtins.int",
                "builtins.float",
                "builtins.bool",
                "builtins.NoneType",
            }
            and value.name == annotation.name
            and value.instance
            and value.exact
            and (annotation.literal is None or annotation.literal == value.literal)
        ):
            return value
        if self._record(annotation.name):
            existing = [v for v in value.variants if v.name == annotation.name]
            if len(existing) == len(value.variants):
                return _join(*existing)
            return _join(*existing, self.validate(annotation.name, value, site))
        if annotation.name in {
            "builtins.list",
            "builtins.set",
            "builtins.frozenset",
            "builtins.dict",
            "builtins.tuple",
        }:
            raw = [self.source.object(v) for v in self.source.expand(value).variants]
            accepted = (
                {"builtins.dict"}
                if annotation.name == "builtins.dict"
                else {
                    "builtins.list",
                    "builtins.tuple",
                    "builtins.set",
                    "builtins.frozenset",
                }
            )
            raw = [v for v in raw if not v.name or v.name in accepted]
            actual_elements = _join(*(v.element() for v in raw))
            arguments = tuple(
                self.normalize(
                    arg,
                    _join(
                        *(
                            v.arguments[index] if len(v.arguments) > index else _Value()
                            for v in raw
                        )
                    )
                    if annotation.name in {"builtins.dict", "builtins.tuple"}
                    and not annotation.variadic
                    else actual_elements,
                    site + f":{index}",
                )
                for index, arg in enumerate(annotation.arguments)
            )
            return self.source.store(
                replace(annotation, exact=True, arguments=arguments, site=site)
            )
        return annotation

    def validate(self, owner: str, data: _Value, site: str) -> _Value:
        if owner in self._validating:
            return _Value(owner, instance=True, unresolved=True)
        self._validating.add(owner)
        try:
            raw = [self.source.object(v) for v in self.source.expand(data).variants]
            raw = [
                v
                for v in raw
                if not v.name or v.name == "builtins.dict" or self.source.model(v.name)
            ]
            fields = []
            for name, annotation in self.fields(owner):
                actual = _join(
                    *(
                        dict(value.fields).get(
                            name,
                            value.arguments[1]
                            if value.name == "builtins.dict"
                            and len(value.arguments) == 2
                            else _Value(),
                        )
                        for value in raw
                    )
                )
                fields.append(
                    (name, self.normalize(annotation, actual, site + ":" + name))
                )
                fields.append(("$input:" + name, actual))
            instance = _Value(
                owner,
                instance=True,
                exact=True,
                initialized=True,
                fields=tuple(fields),
                site=site,
            )
            return self.after(instance)
        finally:
            self._validating.remove(owner)

    def hooks(self, owner: str) -> list[tuple[str, ast.Call]]:
        result = []
        for name in (self.source.lineage(owner) or ([owner], False))[0]:
            definition = self.source.definitions[name]
            assert isinstance(definition.node, ast.ClassDef)
            for node in definition.node.body:
                if isinstance(node, ast.AnnAssign) and isinstance(
                    node.target, ast.Name
                ):
                    scope, annotation = self.source.value(
                        node.annotation, definition.module
                    )
                    if isinstance(annotation, ast.Constant) and isinstance(
                        annotation.value, str
                    ):
                        try:
                            annotation = ast.parse(annotation.value, mode="eval").body
                        except SyntaxError:
                            continue
                    if (
                        isinstance(annotation, ast.Subscript)
                        and self.source.resolve(annotation.value, scope)
                        in {"typing.Annotated", "typing_extensions.Annotated"}
                        and isinstance(annotation.slice, ast.Tuple)
                    ):
                        for metadata in annotation.slice.elts[1:]:
                            validator = self._functional(metadata, scope)
                            if validator is not None:
                                validator_name, mode = validator
                                metadata_decorator = ast.copy_location(
                                    ast.Call(
                                        func=ast.Name(
                                            id="field_validator", ctx=ast.Load()
                                        ),
                                        args=[ast.Constant(value=node.target.id)],
                                        keywords=[
                                            ast.keyword(
                                                arg="mode",
                                                value=ast.Constant(value=mode),
                                            )
                                        ],
                                    ),
                                    node,
                                )
                                result.append((validator_name, metadata_decorator))
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and self.source.resolve(
                        decorator.func, definition.module
                    ) in {
                        "pydantic.field_validator",
                        "pydantic.model_validator",
                        "pydantic.functional_validators.field_validator",
                        "pydantic.functional_validators.model_validator",
                    }:
                        result.append((f"{name}.{node.name}", decorator))
        return result

    def opaque(self, owner: str) -> bool:
        if self._custom_schema(owner):
            return True
        fields = dict(self.fields(owner))
        scalars = {
            "builtins.str",
            "builtins.bytes",
            "builtins.int",
            "builtins.float",
            "builtins.bool",
            "builtins.NoneType",
            "pydantic.SecretStr",
            "pydantic.SecretBytes",
            "pydantic.types.SecretStr",
            "pydantic.types.SecretBytes",
            "uuid.UUID",
            "pathlib.Path",
            "datetime.datetime",
            "datetime.date",
            "datetime.time",
            "decimal.Decimal",
        }
        before_counts: dict[str, int] = {}
        for _, decorator in self.hooks(owner):
            mode = _mode(decorator)
            if mode == "after":
                continue
            if mode != "before" or not decorator.args:
                return True
            targets = [
                node.value
                for node in decorator.args
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            ]
            if len(targets) != len(decorator.args):
                return True
            if targets == ["*"]:
                targets = list(fields)
            for target in targets:
                before_counts[target] = before_counts.get(target, 0) + 1
                if before_counts[target] > 1:
                    return True
            if any(
                value.name not in scalars
                for name in targets
                for value in fields.get(name, _Value()).variants
            ):
                return True
        return False

    def hook_arguments(
        self,
        instance: _Value,
        name: str,
        decorator: ast.Call,
        *,
        values: Mapping[str, _Value] | None = None,
    ) -> list[tuple[str | None, dict[str, _Value]]]:
        function = self.source.definitions[name]
        assert isinstance(function.node, ast.FunctionDef | ast.AsyncFunctionDef)
        parameters = [*function.node.args.posonlyargs, *function.node.args.args]
        targets = (
            [
                arg.value
                for arg in decorator.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ]
            if decorator.args
            else [None]
        )
        if targets == ["*"]:
            targets = [name for name, _ in self.fields(instance.name)]
        result = []
        for target in targets:
            value = (
                (
                    values
                    if values is not None
                    else dict(self.source.object(instance).fields)
                ).get(
                    (
                        "$input:"
                        if _mode(decorator) != "after"
                        else ""
                        if values is not None
                        else "$validator-input:" + name + ":"
                    )
                    + target,
                    dict(self.source.object(instance).fields).get(
                        "$validated:" + target,
                        dict(self.source.object(instance).fields).get(target, _Value()),
                    ),
                )
                if target
                else instance
            )
            bindings = {}
            for index, parameter in enumerate(parameters):
                bindings[parameter.arg] = (
                    _Value(instance.name, exact=True)
                    if index == 0 and parameter.arg == "cls"
                    else value
                    if index == (1 if parameters[0].arg == "cls" else 0)
                    else _Value()
                )
            result.append((target, bindings))
        return result

    def after(self, instance: _Value) -> _Value:
        if self.opaque(instance.name):
            return self.source.store(instance)
        fields = dict(instance.fields)
        if not fields:
            fields = dict(self.source.object(instance).fields)
        for field, annotation in self.fields(instance.name):
            fields["$validated:" + field] = fields.get(field, annotation)
        results = []
        for name, decorator in self.hooks(instance.name):
            if _mode(decorator) != "after":
                continue
            function = self.source.definitions[name]
            if not decorator.args:
                self.source.store(replace(instance, fields=tuple(fields.items())))
            for target, bindings in self.hook_arguments(
                instance, name, decorator, values=fields
            ):
                if target:
                    fields["$validator-input:" + name + ":" + target] = fields.get(
                        target, _Value()
                    )
                returned = self.source.returned(function, bindings)
                if target:
                    fields[target] = returned
                else:
                    results.append(returned)
        return _join(
            self.source.store(replace(instance, fields=tuple(fields.items()))), *results
        )

    def dump(self, instance: _Value, *, json: bool, site: str) -> _Value:
        if json:
            return self.json(site)
        for owner in (self.source.lineage(instance.name) or ([instance.name], False))[
            0
        ]:
            definition = self.source.definitions[owner]
            assert isinstance(definition.node, ast.ClassDef)
            for node in definition.node.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                    self.source.resolve(
                        d.func if isinstance(d, ast.Call) else d, definition.module
                    )
                    in {
                        "pydantic.model_serializer",
                        "pydantic.field_serializer",
                        "pydantic.functional_serializers.model_serializer",
                        "pydantic.functional_serializers.field_serializer",
                    }
                    for d in node.decorator_list
                ):
                    # Python-mode serializers can replace fields or the entire
                    # result with arbitrary application objects.
                    return _Value()
        actual = self.source.object(instance)
        values = dict(actual.fields)
        fields = tuple(
            (name, values.get(name, annotation))
            for name, annotation in self.fields(instance.name)
        )
        return self.source.store(
            _Value(
                "builtins.dict",
                instance=True,
                exact=True,
                fields=fields,
                keys_known=True,
                arguments=(
                    _join(
                        *(
                            _Value(
                                "builtins.str", instance=True, exact=True, literal=name
                            )
                            for name, _ in fields
                        )
                    ),
                    _join(*(v for _, v in fields)),
                ),
                site=site,
            )
        )

    def copy(self, instance: _Value, update: _Value, site: str) -> _Value:
        original = self.source.object(instance)
        fields = {
            name: dict(original.fields).get(name, annotation)
            for name, annotation in self.fields(instance.name)
        }
        for variant in update.variants:
            changes = self.source.object(variant)
            if changes.name == "builtins.dict" and changes.keys_known:
                fields.update(changes.fields)
            elif changes.name != "builtins.NoneType":
                fields = {
                    name: _join(value, _Value()) for name, value in fields.items()
                }
        return self.source.store(
            replace(original, fields=tuple(fields.items()), site=site)
        )

    def _functional(self, node: ast.expr, scope: str) -> tuple[str, str] | None:
        if not isinstance(node, ast.Call) or len(node.args) != 1 or node.keywords:
            return None
        factory = self.source.libraries.canonical(
            self.source.resolve(node.func, scope) or ""
        )
        modes = {
            "pydantic.functional_validators.BeforeValidator": "before",
            "pydantic.functional_validators.AfterValidator": "after",
        }
        function = self.source.definitions.get(
            self.source.resolve(node.args[0], scope) or ""
        )
        if factory not in modes or function is None or function.owner is not None:
            return None
        if (
            not isinstance(function.node, ast.FunctionDef)
            or function.node.decorator_list
        ):
            return None
        return function.name, modes[factory]

    def _record(self, owner: str) -> bool:
        if self.source.model(owner):
            return True
        definition = self.source.definitions.get(owner)
        if definition is None or not isinstance(definition.node, ast.ClassDef):
            return False
        node = definition.node
        if (
            node.bases
            or node.keywords
            or node.type_params
            or len(node.decorator_list) != 1
        ):
            return False
        decorator = node.decorator_list[0]
        if (
            self.source.resolve(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                definition.module,
            )
            != "dataclasses.dataclass"
        ):
            return False
        if isinstance(decorator, ast.Call) and (
            decorator.args
            or any(
                kw.arg is None
                or not isinstance(kw.value, ast.Constant)
                or not isinstance(kw.value.value, bool)
                for kw in decorator.keywords
            )
        ):
            return False
        if any(
            self.source.method(owner, name)
            for name in (
                "__init__",
                "__new__",
                "__getattribute__",
                "__getattr__",
                "__setattr__",
            )
        ):
            return False
        if self.hooks(owner):
            return False
        for field in node.body:
            if not isinstance(field, ast.AnnAssign):
                continue
            if (
                isinstance(field.annotation, ast.Subscript)
                and self.source.resolve(field.annotation.value, definition.module)
                == "dataclasses.InitVar"
            ):
                return False
            if field.value is None:
                continue
            scope, default = self.source.value(field.value, definition.module)
            if isinstance(default, ast.Constant):
                continue
            if isinstance(default, ast.Attribute):
                enum = self.source.resolve(default.value, scope) or ""
                if "enum.Enum" in (self.source.mro(enum) or ()):
                    continue
            return False
        return True

    def _custom_schema(self, owner: str, seen: frozenset[str] = frozenset()) -> bool:
        definition = self.source.definitions.get(owner)
        if definition is None:
            return False
        if not isinstance(definition.node, ast.ClassDef):
            return True
        if any(keyword.arg == "metaclass" for keyword in definition.node.keywords):
            return True
        if owner in seen:
            return False
        seen = seen | {owner}
        if any(
            self.source.method(owner, name)
            for name in (
                "__get_pydantic_core_schema__",
                "__get_validators__",
                "_missing_",
            )
        ):
            return True
        for name in (self.source.lineage(owner) or ([owner], False))[0]:
            definition = self.source.definitions.get(name)
            if definition is None or not isinstance(definition.node, ast.ClassDef):
                continue
            for statement in definition.node.body:
                if not isinstance(statement, ast.AnnAssign):
                    continue
                if (
                    isinstance(statement.value, ast.Call)
                    and self.source.libraries.canonical(
                        self.source.resolve(statement.value.func, definition.module)
                        or ""
                    )
                    == "pydantic.fields.Field"
                    and any(keyword.arg is None for keyword in statement.value.keywords)
                ):
                    return True
                scope, annotation = self.source.value(
                    statement.annotation, definition.module
                )
                if isinstance(annotation, ast.Constant) and isinstance(
                    annotation.value, str
                ):
                    try:
                        annotation = ast.parse(annotation.value, mode="eval").body
                    except SyntaxError:
                        return True
                for node in ast.walk(annotation):
                    if isinstance(node, ast.Subscript) and self.source.resolve(
                        node.value, scope
                    ) in {"typing.Annotated", "typing_extensions.Annotated"}:
                        metadata = (
                            node.slice.elts[1:]
                            if isinstance(node.slice, ast.Tuple)
                            else []
                        )
                        if any(
                            isinstance(child, ast.Name | ast.Attribute)
                            and (
                                self.source.resolve(child, scope)
                                in self.source.definitions
                                or (self.source.resolve(child, scope) or "").rsplit(
                                    ".", 1
                                )[-1]
                                in {
                                    "BeforeValidator",
                                    "AfterValidator",
                                    "PlainValidator",
                                    "WrapValidator",
                                    "SkipValidation",
                                    "ValidateAs",
                                    "InstanceOf",
                                }
                            )
                            for value in metadata
                            if not (
                                node is annotation
                                and self._functional(value, scope) is not None
                                and sum(
                                    self._functional(item, scope) is not None
                                    for item in metadata
                                )
                                == 1
                            )
                            for child in ast.walk(value)
                        ):
                            return True
                    if (
                        isinstance(node, ast.Call)
                        and self.source.resolve(node.func, scope)
                        in self.source.definitions
                    ):
                        return True
        for _, value in self.fields(owner):
            pending = list(value.variants)
            for child in pending:
                pending.extend(child.arguments)
                if child.name in self.source.definitions and self._custom_schema(
                    child.name, seen
                ):
                    return True
        return False


def _mode(decorator: ast.Call) -> str | None:
    if any(kw.arg is None for kw in decorator.keywords):
        return None
    node = next((kw.value for kw in decorator.keywords if kw.arg == "mode"), None)
    if node is None:
        return "after"
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )
