"""Transport-independent annotations for values supplied by Canon."""

from typing import Annotated, Any, TypeAliasType, get_args, get_origin


class _Injection:
    __slots__ = ()


_MARKER = _Injection()

type Inject[T] = Annotated[T, _MARKER]


def injected_dependency(annotation: Any) -> Any | None:
    """Return the dependency named by an injection annotation or alias."""
    while isinstance(annotation, TypeAliasType):
        annotation = annotation.__value__
    if get_origin(annotation) is Inject:
        return get_args(annotation)[0]
    if get_origin(annotation) is Annotated:
        dependency, *metadata = get_args(annotation)
        if _MARKER in metadata:
            return dependency
        return injected_dependency(dependency)
    return None
