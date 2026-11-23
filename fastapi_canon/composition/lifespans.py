from __future__ import annotations

import sys
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from inspect import (
    Parameter,
    Signature,
    isclass,
    isfunction,
    ismethod,
    signature,
    unwrap,
)
from typing import Any, get_type_hints

from fastapi import FastAPI

from fastapi_canon.errors import FeatureConfigurationError
from fastapi_canon.routing.injection import injected_dependency

type DeclaredLifespan = Callable[
    ..., AbstractAsyncContextManager[Mapping[str, Any] | None]
]
"""A feature lifespan taking the application, ``Inject[T]`` services, or both."""

type ApplicationLifespan = Callable[
    [FastAPI], AbstractAsyncContextManager[Mapping[str, Any] | None]
]


@dataclass(frozen=True, slots=True)
class ParameterInjection:
    """The services one callable injects from the APP container."""

    dependencies: tuple[tuple[str, Any], ...]
    app_parameter: Parameter | None


def bind_lifespan(
    lifespan: DeclaredLifespan, *, feature_name: str
) -> ApplicationLifespan:
    """Return *lifespan* as an application lifespan that resolves its services."""
    injection = lifespan_injection(lifespan, feature_name=feature_name)
    if not injection.dependencies:
        return lifespan

    @asynccontextmanager
    async def injected(app: FastAPI) -> AsyncGenerator[Mapping[str, Any] | None]:
        container = getattr(app.state, "dishka_container", None)
        if container is None:
            raise RuntimeError(
                f"Feature {feature_name!r} lifespan uses Inject[...] and needs a "
                "Dishka container. Add providers to the Composition, or call "
                "dishka.integrations.fastapi.setup_dishka(container, app)."
            )
        arguments = {
            name: await container.get(dependency)
            for name, dependency in injection.dependencies
        }
        positional: tuple[FastAPI, ...] = ()
        parameter = injection.app_parameter
        if parameter is not None and parameter.kind is Parameter.POSITIONAL_ONLY:
            positional = (app,)
        elif parameter is not None:
            arguments[parameter.name] = app
        async with lifespan(*positional, **arguments) as state:
            yield state

    return injected


def lifespan_injection(
    lifespan: DeclaredLifespan, *, feature_name: str
) -> ParameterInjection:
    """Describe the ``Inject[T]`` parameters of *lifespan*.

    A lifespan without them keeps the plain ``lifespan(app)`` contract.
    """
    return callable_injection(lifespan, owner=f"feature {feature_name!r} lifespan")


def callable_injection(
    target: Callable[..., Any],
    *,
    owner: str,
    supplied: frozenset[str] = frozenset(),
) -> ParameterInjection:
    """Describe the ``Inject[T]`` parameters of *target*.

    Parameters named in *supplied* receive explicit arguments, so they do not count
    as required. *owner* names the callable in configuration errors.
    """
    parameters = tuple(read_signature(target).parameters.values())
    required = tuple(
        parameter
        for parameter in parameters
        if parameter.default is Parameter.empty
        and parameter.name not in supplied
        and parameter.kind not in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD)
    )
    try:
        hints = _parameter_hints(_annotated(target), parameters)
    except (NameError, TypeError) as error:
        if len(required) <= 1:
            return ParameterInjection((), None)
        msg = f"{owner} annotations cannot be resolved: {error}"
        raise FeatureConfigurationError(msg) from error
    injected = tuple(
        (parameter, dependency)
        for parameter in parameters
        if (dependency := injected_dependency(hints.get(parameter.name))) is not None
    )
    if not injected:
        return ParameterInjection((), None)
    unsupported = tuple(
        parameter.name
        for parameter, _ in injected
        if parameter.kind
        not in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
    )
    if unsupported:
        msg = f"{owner} must accept {', '.join(unsupported)} by keyword to inject it"
        raise FeatureConfigurationError(msg)
    names = {parameter.name for parameter, _ in injected}
    others = tuple(parameter for parameter in required if parameter.name not in names)
    if len(others) > 1:
        msg = (
            f"{owner} takes the application and "
            f"Inject[...] services; {', '.join(p.name for p in others)} are neither"
        )
        raise FeatureConfigurationError(msg)
    return ParameterInjection(
        tuple((parameter.name, dependency) for parameter, dependency in injected),
        others[0] if others else None,
    )


def read_signature(target: Callable[..., Any]) -> Signature:
    """Read the signature without evaluating deferred annotations on Python 3.14."""
    if sys.version_info >= (3, 14):
        from inspect import Format  # annotationlib only exists on Python 3.14.

        return signature(target, annotation_format=Format.FORWARDREF)
    return signature(target)


def _parameter_hints(
    annotated: Any, parameters: tuple[Parameter, ...]
) -> dict[str, Any]:
    """Resolve parameter annotations; a type-only return must not affect them."""

    def holder() -> None: ...

    holder.__annotations__ = {
        parameter.name: parameter.annotation
        for parameter in parameters
        if parameter.annotation is not Parameter.empty
    }
    namespace = getattr(unwrap(annotated), "__globals__", {})
    return get_type_hints(holder, globalns=namespace, include_extras=True)


def _annotated(target: Callable[..., Any]) -> Any:
    if isfunction(target) or ismethod(target):
        return target
    if isclass(target):
        return target.__init__
    return type(target).__call__
