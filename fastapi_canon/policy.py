import inspect
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, TypeAliasType, get_args, get_origin

from fastapi.params import Depends

from fastapi_canon.error import Error, ErrorConfigurationError
from fastapi_canon.error.registry import AnyError
from fastapi_canon.error.validation import unique_instances

_DECLARED_ERRORS = "__canon_raises__"


@dataclass(frozen=True, slots=True, init=False)
class RouterPolicy:
    """Reusable HTTP prerequisites and their declared error contracts.

    Dependencies execute through FastAPI; ``raises`` documents possible errors.
    Route classes remain a declaration-time choice on each router.
    """

    dependencies: tuple[Depends, ...]
    raises: tuple[AnyError, ...]

    def __init__(
        self,
        *,
        dependencies: Sequence[Depends] = (),
        raises: Sequence[AnyError] = (),
    ) -> None:
        raw_dependencies: object = dependencies
        if isinstance(raw_dependencies, str) or not isinstance(
            raw_dependencies, Sequence
        ):
            msg = "dependencies must be a sequence of Depends instances"
            raise ErrorConfigurationError(msg)
        normalized_dependencies = tuple(
            unique_instances(dependencies, Depends, parameter="dependencies")
        )
        normalized_raises = normalize_raises(raises, parameter="raises")
        object.__setattr__(self, "dependencies", normalized_dependencies)
        object.__setattr__(self, "raises", normalized_raises)


def raises[DependencyT](*errors: AnyError) -> Callable[[DependencyT], DependencyT]:
    """Declare the errors a FastAPI dependency can raise.

    Every HTTP operation, typed stream and WebRTC session that uses the
    dependency, directly or through other dependencies, routers or policies,
    documents and registers these errors as if it declared them in ``raises=``.
    The dependency itself is returned unchanged apart from the declaration.
    """
    declared = normalize_raises(errors, parameter="raises")
    if not declared:
        raise ErrorConfigurationError("raises() expects at least one Error")

    def declare(dependency: DependencyT) -> DependencyT:
        if not callable(dependency):
            raise ErrorConfigurationError("raises() decorates a callable dependency")
        existing: tuple[AnyError, ...] = getattr(dependency, _DECLARED_ERRORS, ())
        try:
            setattr(
                dependency,
                _DECLARED_ERRORS,
                tuple(
                    unique_instances((*existing, *declared), Error, parameter="raises")
                ),
            )
        except (AttributeError, TypeError) as error:
            msg = f"raises() cannot annotate {dependency!r}; decorate its function"
            raise ErrorConfigurationError(msg) from error
        return dependency

    return declare


def dependency_errors(
    dependencies: Iterable[Depends] = (),
    *,
    call: Callable[..., object] | None = None,
) -> tuple[AnyError, ...]:
    """Return errors declared with ``raises()`` by dependencies and sub-dependencies.

    ``call`` contributes the dependencies of its own parameters; its own
    declaration is not read, because operations declare errors with ``raises=``.
    """
    found: dict[int, AnyError] = {}
    seen: set[int] = set()
    for dependency in dependencies:
        _visit(dependency.dependency, found, seen)
    if call is not None:
        for target in _parameter_dependencies(call):
            _visit(target, found, seen)
    return tuple(found.values())


def validate_policy(value: RouterPolicy | None) -> None:
    raw: object = value
    if raw is not None and not isinstance(raw, RouterPolicy):
        msg = "policy must be a RouterPolicy instance or None"
        raise ErrorConfigurationError(msg)


def normalize_raises(
    values: Sequence[AnyError],
    *,
    parameter: str,
) -> tuple[AnyError, ...]:
    raw_values: object = values
    if isinstance(raw_values, str) or not isinstance(raw_values, Sequence):
        msg = f"{parameter} must be a sequence of Error instances"
        raise ErrorConfigurationError(msg)
    return tuple(unique_instances(values, Error, parameter=parameter))


def _visit(target: object, found: dict[int, AnyError], seen: set[int]) -> None:
    if target is None or id(target) in seen:
        return
    seen.add(id(target))
    declared: object = getattr(target, _DECLARED_ERRORS, ())
    if isinstance(declared, tuple):
        for error in declared:
            if isinstance(error, Error):
                found.setdefault(id(error), error)
    if callable(target):
        for child in _parameter_dependencies(target):
            _visit(child, found, seen)


def _parameter_dependencies(call: Callable[..., object]) -> Iterator[object]:
    try:
        signature = inspect.signature(call, eval_str=True)
    except (NameError, SyntaxError, TypeError, ValueError):
        # Unresolvable annotations still allow Depends() defaults to be read.
        try:
            signature = inspect.signature(call)
        except (TypeError, ValueError):
            return
    for parameter in signature.parameters.values():
        annotation: Any = parameter.annotation
        if isinstance(annotation, TypeAliasType):
            annotation = annotation.__value__
        markers: list[Depends] = []
        base: object = annotation
        if get_origin(annotation) is Annotated:
            base, *metadata = get_args(annotation)
            markers.extend(item for item in metadata if isinstance(item, Depends))
        if isinstance(parameter.default, Depends):
            markers.append(parameter.default)
        for marker in markers:
            # Depends() without a callable resolves the annotated class itself.
            yield marker.dependency if marker.dependency is not None else base
