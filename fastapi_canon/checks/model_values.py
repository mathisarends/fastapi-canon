"""Abstract values guaranteed by Pydantic's validation and serialization APIs."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import replace
from typing import Protocol

from fastapi_canon.checks.symbols import _Definition
from fastapi_canon.checks.value_flow import _join, _Value


class _ModelSource(Protocol):
    """Source operations required by model value contracts, independent of indexing."""

    heap: dict[str, _Value]
    revision: int

    @property
    def definitions(self) -> Mapping[str, _Definition]: ...

    def lineage(self, name: str) -> tuple[list[str], bool] | None: ...
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
        if not annotation.name:
            return value
        if self.source.model(annotation.name):
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
            instance = self.source.store(
                _Value(
                    owner,
                    instance=True,
                    exact=True,
                    initialized=True,
                    fields=tuple(fields),
                    site=site,
                )
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
        return any(
            any(
                kw.arg == "mode"
                and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value == "after"
                )
                for kw in decorator.keywords
            )
            for _, decorator in self.hooks(owner)
        )

    def hook_arguments(
        self, instance: _Value, name: str, decorator: ast.Call
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
                dict(self.source.object(instance).fields).get(target, _Value())
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
        results = [instance]
        if self.opaque(instance.name):
            return instance
        for name, decorator in self.hooks(instance.name):
            function = self.source.definitions[name]
            for target, bindings in self.hook_arguments(instance, name, decorator):
                returned = self.source.returned(function, bindings)
                if target:
                    actual = self.source.object(instance)
                    fields = dict(actual.fields)
                    fields[target] = returned
                    self.source.store(replace(actual, fields=tuple(fields.items())))
                else:
                    results.append(returned)
        return _join(*results)

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
