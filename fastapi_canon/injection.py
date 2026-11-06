"""Transport-independent annotations for values supplied by Canon.

``Inject[T]`` marks a parameter whose value comes from the application's Dishka
container. RPC methods, MCP tools and WebRTC factories read the marker. In
FastAPI, the annotation also carries a ``Depends`` resolver, so ``Inject[T]``
works in endpoint parameters and in ordinary FastAPI dependencies alike.
"""

from functools import cache
from typing import TYPE_CHECKING, Annotated, Any, TypeAliasType, get_args, get_origin

from fastapi import Depends
from starlette.requests import HTTPConnection


class _Injection:
    __slots__ = ()


_MARKER = _Injection()

if TYPE_CHECKING:
    type Inject[T] = Annotated[T, _MARKER]
else:

    class Inject:
        """Annotate a parameter as ``Inject[Service]``."""

        __slots__ = ()

        def __class_getitem__(cls, dependency: Any) -> Any:
            return _annotation(dependency)


def injected_dependency(annotation: Any) -> Any | None:
    """Return the dependency named by an injection annotation or alias."""
    while isinstance(annotation, TypeAliasType):
        annotation = annotation.__value__
    if get_origin(annotation) is Annotated:
        dependency, *metadata = get_args(annotation)
        if _MARKER in metadata:
            return dependency
        return injected_dependency(dependency)
    return None


def injected_resolver_dependency(dependency: object) -> Any | None:
    """Return the service resolved by an ``Inject[T]`` FastAPI resolver, if any."""
    return getattr(dependency, "__canon_injected_dependency__", None)


def _annotation(dependency: Any) -> Any:
    try:
        return _resolved_annotation(dependency)
    except TypeError:  # unhashable type expressions cannot be cached
        return _build_annotation(dependency)


@cache
def _resolved_annotation(dependency: Any) -> Any:
    return _build_annotation(dependency)


def _build_annotation(dependency: Any) -> Any:
    if isinstance(dependency, str):
        # A string names no type yet; transports resolve it with the type hints.
        return Annotated[dependency, _MARKER]
    return Annotated[dependency, _MARKER, Depends(_resolver(dependency))]


def _resolver(dependency: Any) -> Any:
    async def resolve(connection: HTTPConnection) -> Any:
        container = getattr(connection.state, "dishka_container", None)
        if container is None:
            raise RuntimeError(
                "Inject[...] and FromDishka[...] need a Dishka request container. "
                "Install the router with Composition(...).build(app), or call "
                "dishka.integrations.fastapi.setup_dishka(container, app)."
            )
        return await container.get(dependency)

    resolve.__name__ = resolve.__qualname__ = f"inject_{_name(dependency)}"
    resolve.__dict__["__canon_injected_dependency__"] = dependency
    return resolve


def _name(dependency: Any) -> str:
    return str(getattr(dependency, "__name__", "dependency"))
