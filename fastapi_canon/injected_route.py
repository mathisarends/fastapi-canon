"""HTTP routes that resolve ``Inject[T]`` and ``FromDishka[T]`` parameters."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from inspect import Parameter
from typing import Annotated, Any, cast, get_type_hints

from dishka import AsyncContainer, DependencyKey
from dishka.entities.component import DEFAULT_COMPONENT
from dishka.integrations.base import default_parse_dependency, wrap_injection
from fastapi import Depends, Request
from fastapi.routing import APIRoute

from fastapi_canon.injection import injected_dependency


class InjectedRoute(APIRoute):
    """Resolve endpoint dependencies from the request's Dishka container.

    Behaves like Dishka's ``DishkaRoute`` and additionally accepts Canon's
    transport-independent ``Inject[T]`` marker. Endpoints without injected
    parameters, including those already decorated with ``@inject``, are
    registered unchanged.
    """

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(path, inject_endpoint(endpoint), **kwargs)


def inject_endpoint[EndpointT: Callable[..., Any]](endpoint: EndpointT) -> EndpointT:
    """Return *endpoint* with injected parameters resolved from Dishka."""
    hints = get_type_hints(endpoint, include_extras=True)
    parameters = inspect.signature(endpoint).parameters
    if not any(
        _parse_dependency(parameter, hints.get(name, Any)) is not None
        for name, parameter in parameters.items()
    ):
        return endpoint
    if inspect.iscoroutinefunction(endpoint) or inspect.isasyncgenfunction(endpoint):
        return _inject_async(endpoint, hints, parameters)
    return _inject_sync(endpoint, hints, parameters)


def _parse_dependency(parameter: Parameter, hint: Any) -> DependencyKey | None:
    dependency = injected_dependency(hint)
    if dependency is not None:
        return DependencyKey(dependency, DEFAULT_COMPONENT)
    return default_parse_dependency(parameter, hint)


def _inject_async[EndpointT: Callable[..., Any]](
    endpoint: EndpointT, hints: dict[str, Any], parameters: Any
) -> EndpointT:
    # FastAPI supplies the request to only one parameter, so reuse an existing one.
    request_name = next(
        (name for name in parameters if hints.get(name) is Request), None
    )
    additional_params: list[Parameter] = []
    if request_name is None:
        request_name = "___canon_request"
        while request_name in parameters:
            request_name = "_" + request_name
        additional_params.append(
            Parameter(request_name, Parameter.KEYWORD_ONLY, annotation=Request)
        )
    name = request_name
    injected = wrap_injection(
        func=endpoint,
        is_async=True,
        additional_params=additional_params,
        parse_dependency=_parse_dependency,
        container_getter=lambda _, values: _request_container(values[name]),
    )
    return cast("EndpointT", injected)


def _inject_sync[EndpointT: Callable[..., Any]](
    endpoint: EndpointT, hints: dict[str, Any], parameters: Any
) -> EndpointT:
    # Synchronous endpoints run in a worker thread, so their dependencies are
    # resolved by FastAPI beforehand, as Dishka's own route does.
    replaced: list[Parameter] = []
    annotations = dict(hints)
    for name, parameter in parameters.items():
        hint = hints.get(name, parameter.annotation)
        dependency = _parse_dependency(parameter, hint)
        if dependency is not None:
            hint = Annotated[dependency.type_hint, Depends(_resolver(dependency))]
            annotations[name] = hint
        replaced.append(parameter.replace(annotation=hint))

    if inspect.isgeneratorfunction(endpoint):

        def generator(*args: Any, **kwargs: Any) -> Any:
            yield from endpoint(*args, **kwargs)

        wrapper: Callable[..., Any] = generator
    else:

        def function(*args: Any, **kwargs: Any) -> Any:
            return endpoint(*args, **kwargs)

        wrapper = function
    functools.update_wrapper(wrapper, endpoint)
    wrapper.__signature__ = inspect.signature(endpoint).replace(  # type: ignore[attr-defined]
        parameters=replaced
    )
    wrapper.__annotations__ = annotations
    return wrapper  # type: ignore[return-value]


def _resolver(dependency: DependencyKey) -> Callable[[Request], Any]:
    async def resolve(request: Request) -> Any:
        return await _request_container(request).get(
            dependency.type_hint, component=dependency.component
        )

    return resolve


def _request_container(request: Request) -> AsyncContainer:
    container: AsyncContainer | None = getattr(request.state, "dishka_container", None)
    if container is None:
        raise RuntimeError(
            "Inject[...] and FromDishka[...] need a Dishka request container. "
            "Install the router with Composition(...).build(app), or call "
            "dishka.integrations.fastapi.setup_dishka(container, app)."
        )
    return container
