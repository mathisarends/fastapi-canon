"""Value effects of native container methods, without executing their contents."""

from dataclasses import dataclass, replace

from fastapi_canon.checks.value_flow import _dictionary_union, _join, _Value

_MUTATORS = {
    "builtins.list": frozenset(
        {"append", "extend", "insert", "pop", "remove", "clear", "reverse", "sort"}
    ),
    "builtins.set": frozenset(
        {
            "add",
            "update",
            "symmetric_difference_update",
            "pop",
            "remove",
            "discard",
            "clear",
            "difference_update",
            "intersection_update",
        }
    ),
    "builtins.dict": frozenset({"update", "setdefault", "clear"}),
}


@dataclass(frozen=True)
class _Mutation:
    container: _Value
    result: _Value
    uncertain: bool = False


def _mutate(value: _Value, method: str, args: tuple[_Value, ...]) -> _Mutation | None:
    if method not in _MUTATORS.get(value.name, ()):
        return None
    none = _Value("builtins.NoneType", instance=True, exact=True)
    if value.name in {"builtins.list", "builtins.set"}:
        if method in {"append", "add", "insert"}:
            index = 1 if method == "insert" else 0
            if len(args) != index + 1:
                return _Mutation(value, none, True)
            return _Mutation(
                replace(value, arguments=(_join(value.element(), args[index]),)), none
            )
        if method in {"extend", "update", "symmetric_difference_update"}:
            native = {
                "builtins.list",
                "builtins.tuple",
                "builtins.set",
                "builtins.frozenset",
                "builtins.dict",
            }
            elements = [v.element() if v.name in native else _Value() for v in args]
            return _Mutation(
                replace(value, arguments=(_join(value.element(), *elements),)),
                none,
                any(v.name not in native for v in args),
            )
        if method in {
            "discard",
            "remove",
            "clear",
            "reverse",
            "sort",
            "difference_update",
            "intersection_update",
        }:
            # Deletion cannot introduce values. Weak heap updates retain values
            # that may be read by another operation before the deletion.
            return _Mutation(
                value,
                none,
                method in {"sort", "difference_update", "intersection_update"},
            )
        if method == "pop":
            return _Mutation(value, value.element())
    if value.name == "builtins.dict":
        if method == "update":
            if not args:
                return _Mutation(value, none)
            if len(args) != 1 or args[0].name != "builtins.dict":
                return _Mutation(
                    replace(value, arguments=(_Value(), _Value()), keys_known=False),
                    none,
                    True,
                )
            return _Mutation(
                replace(_dictionary_union(value, args[0]), site=value.site), none
            )
        if method == "setdefault":
            if not 1 <= len(args) <= 2:
                return _Mutation(value, _Value(), True)
            key = args[0]
            default = args[1] if len(args) == 2 else none
            fields = dict(value.fields)
            if isinstance(key.literal, str) and key.literal in fields:
                return _Mutation(value, fields[key.literal])
            result = (
                default
                if value.keys_known
                else _join(
                    value.arguments[1] if len(value.arguments) == 2 else _Value(),
                    default,
                )
            )
            if isinstance(key.literal, str):
                fields[key.literal] = result
            return _Mutation(
                replace(
                    value,
                    arguments=(
                        _join(
                            value.arguments[0]
                            if len(value.arguments) == 2
                            else _Value(),
                            key,
                        ),
                        _join(
                            value.arguments[1]
                            if len(value.arguments) == 2
                            else _Value(),
                            default,
                        ),
                    ),
                    fields=tuple(fields.items()),
                    keys_known=value.keys_known and isinstance(key.literal, str),
                ),
                result,
            )
        if method == "clear":
            return _Mutation(value, none)
    return None
