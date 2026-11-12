"""Immutable abstract values and Python argument binding; inspected code never runs."""

import ast
import inspect
from dataclasses import dataclass, replace
from itertools import product


@dataclass(frozen=True)
class _Value:
    name: str = ""
    instance: bool = False
    receiver: str | None = None
    arguments: tuple["_Value", ...] = ()
    fields: tuple[tuple[str, "_Value"], ...] = ()
    bound: "_Value | None" = None
    alternatives: tuple["_Value", ...] = ()
    exact: bool = False
    initialized: bool = False
    dependency: bool = False
    site: str = ""
    literal: str | int | float | bool | None = None
    hint: "_Value | None" = None
    unresolved: bool = False
    variadic: bool = False
    keys_known: bool = False

    @property
    def variants(self) -> tuple["_Value", ...]:
        return self.alternatives or (self,)

    def element(self) -> "_Value":
        if self.alternatives:
            return _join(*(value.element() for value in self.alternatives))
        if self.name == "builtins.tuple":
            return _join(*self.arguments)
        return self.arguments[0] if self.arguments else _Value()

    def reference(self) -> "_Value":
        if self.instance and self.site:
            return replace(
                self,
                fields=(),
                arguments=(),
                hint=None,
                initialized=False,
                keys_known=False,
                variadic=False,
            )
        return replace(
            self,
            arguments=tuple(v.reference() for v in self.arguments),
            fields=tuple((k, v.reference()) for k, v in self.fields),
            bound=self.bound.reference() if self.bound else None,
            alternatives=tuple(v.reference() for v in self.alternatives),
        )


@dataclass(frozen=True)
class _ValueSummary:
    modules: frozenset[str]
    reads: tuple[tuple[str, _Value | None], ...]


def _dictionary_union(left: _Value, right: _Value) -> _Value:
    """Copy dictionary contents, retaining right-hand precedence when known."""
    fields = dict(left.fields)
    if not right.keys_known:
        other = right.arguments[1] if len(right.arguments) == 2 else _Value()
        fields = {key: _join(value, other) for key, value in fields.items()}
    fields.update(right.fields)
    known = left.keys_known and right.keys_known
    keys = (
        _join(*(_Value("builtins.str", instance=True, literal=key) for key in fields))
        if known
        else _join(
            *(
                item.arguments[0] if len(item.arguments) == 2 else _Value()
                for item in (left, right)
            )
        )
    )
    values = (
        _join(*fields.values())
        if known
        else _join(
            *(
                item.arguments[1] if len(item.arguments) == 2 else _Value()
                for item in (left, right)
            )
        )
    )
    return _Value(
        "builtins.dict",
        instance=True,
        exact=True,
        fields=tuple(fields.items()),
        arguments=(keys, values),
        keys_known=known,
    )


def _join(*values: _Value) -> _Value:
    variants = tuple(
        dict.fromkeys(
            child
            for value in values
            for child in value.variants
            if child.name != "<empty>"
        )
    )
    if not variants:
        return _Value("<empty>")
    return variants[0] if len(variants) == 1 else _Value(alternatives=variants)


def _contexts(
    bindings: dict[str, _Value], limit: int = 32
) -> tuple[dict[str, _Value], ...] | None:
    choices = {}
    size = 1
    for name, value in bindings.items():
        types: dict[str, list[_Value]] = {}
        for variant in value.variants:
            types.setdefault(variant.name, []).append(variant)
        choices[name] = tuple(_join(*group) for group in types.values())
        size *= len(choices[name])
        if size > limit:
            return None
    if size == 1:
        return (bindings,)
    return tuple(
        dict(zip(bindings, values, strict=True))
        for values in product(*choices.values())
    )


def _bind(
    arguments: ast.arguments,
    positional: tuple[_Value, ...],
    keywords: tuple[tuple[str, _Value], ...],
    defaults: tuple[_Value, ...],
    keyword_defaults: tuple[_Value | None, ...],
) -> dict[str, _Value] | None:
    """Apply Python's binding rules to values, without evaluating a callable."""
    parameters: list[inspect.Parameter] = []
    ordinary = [*arguments.posonlyargs, *arguments.args]
    offset = len(ordinary) - len(defaults)
    for index, argument in enumerate(ordinary):
        parameters.append(
            inspect.Parameter(
                argument.arg,
                inspect.Parameter.POSITIONAL_ONLY
                if index < len(arguments.posonlyargs)
                else inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=defaults[index - offset]
                if index >= offset
                else inspect.Parameter.empty,
            )
        )
    if arguments.vararg:
        parameters.append(
            inspect.Parameter(arguments.vararg.arg, inspect.Parameter.VAR_POSITIONAL)
        )
    for argument, default in zip(arguments.kwonlyargs, keyword_defaults, strict=True):
        parameters.append(
            inspect.Parameter(
                argument.arg,
                inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if default is None else default,
            )
        )
    if arguments.kwarg:
        parameters.append(
            inspect.Parameter(arguments.kwarg.arg, inspect.Parameter.VAR_KEYWORD)
        )
    if len({name for name, _ in keywords}) != len(keywords):
        return None
    try:
        bound = inspect.Signature(parameters).bind(*positional, **dict(keywords))
    except TypeError:
        return None
    bound.apply_defaults()
    result: dict[str, _Value] = {}
    for name, value in bound.arguments.items():
        if isinstance(value, tuple):
            result[name] = _Value(
                "builtins.tuple", instance=True, arguments=value, exact=True
            )
        elif isinstance(value, dict):
            result[name] = _Value(
                "builtins.dict",
                instance=True,
                fields=tuple(value.items()),
                exact=True,
                keys_known=True,
            )
        else:
            result[name] = value
    return result
