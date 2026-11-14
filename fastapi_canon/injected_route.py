"""HTTP routes that resolve ``Inject[T]`` and ``FromDishka[T]`` parameters."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from contextlib import aclosing
from inspect import Parameter
from typing import Annotated, Any, cast, get_type_hints

from dishka import DEFAULT_COMPONENT, AsyncContainer, DependencyKey
from dishka.integrations.base import default_parse_dependency, wrap_injection
from fastapi import Depends, Request
from fastapi.routing import APIRoute
from starlette.routing import get_name

from fastapi_canon.injection import injected_dependency


class InjectedRoute(APIRoute):
    """Resolve endpoint dependencies from the request's Dishka container.

    Behaves like Dishka's ``DishkaRoute`` and additionally accepts Canon's
    transport-independent ``Inject[T]`` marker. Endpoints without injected
    parameters, including ordinary functions already decorated with ``@inject``,
    keep their behavior. Typed callable objects, bound methods and partials use
    independent function adapters without changing the declared lookup identity.
    """

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(path, inject_endpoint(endpoint), **kwargs)


def inject_endpoint[EndpointT: Callable[..., Any]](endpoint: EndpointT) -> EndpointT:
    """Return *endpoint* with injected parameters resolved from Dishka."""
    signature, hints, source = _endpoint_signature(endpoint)
    parameters = signature.parameters
    if not inspect.isfunction(endpoint) and not inspect.isclass(endpoint):
        endpoint = cast(
            "EndpointT", _adapt_endpoint(endpoint, signature, hints, source)
        )
    if not any(
        _parse_dependency(parameter, hints.get(name, parameter.annotation)) is not None
        for name, parameter in parameters.items()
    ):
        return endpoint
    if inspect.isclass(endpoint):
        raise TypeError(
            "Endpoint constructors with injection need a typed function or callable instance"
        )
    if inspect.iscoroutinefunction(endpoint) or inspect.isasyncgenfunction(endpoint):
        wrapped = _inject_async(endpoint, parameters)
    else:
        wrapped = _inject_sync(endpoint, hints, parameters)
    wrapped.__dict__["__canon_declared_endpoint__"] = getattr(
        endpoint, "__canon_declared_endpoint__", endpoint
    )
    return wrapped


def endpoint_dependencies(endpoint: Callable[..., Any]) -> tuple[DependencyKey, ...]:
    retained = getattr(endpoint, "__canon_http_dependency_roots__", None)
    if retained is not None:
        return cast("tuple[DependencyKey, ...]", retained)
    source = getattr(endpoint, "__dishka_orig_func__", endpoint)
    signature, hints, _ = _endpoint_signature(source)
    return tuple(
        dict.fromkeys(
            dependency
            for name, parameter in signature.parameters.items()
            if (
                dependency := _parse_dependency(
                    parameter, hints.get(name, parameter.annotation)
                )
            )
            is not None
        )
    )


def _endpoint_signature(
    endpoint: Callable[..., Any],
) -> tuple[inspect.Signature, dict[str, Any], Callable[..., Any]]:
    try:
        signature = inspect.signature(endpoint)
        source = endpoint
        while isinstance(source, functools.partial):
            source = source.func
        owner: object | None = None
        if inspect.ismethod(source):
            owner = source.__self__
        elif not (
            inspect.isfunction(source)
            or inspect.isclass(source)
            or inspect.isbuiltin(source)
        ):
            owner = source
            method = inspect.getattr_static(type(source), "__call__")
            source = cast("Callable[..., Any]", method.__get__(source, type(source)))
        namespace: dict[str, Any] = {}
        if owner is not None:
            cls = owner if inspect.isclass(owner) else type(owner)
            for base in reversed(inspect.getmro(cls)):
                namespace.update(vars(base))
        hints = get_type_hints(source, localns=namespace or None, include_extras=True)
    except (TypeError, ValueError, NameError, AttributeError):
        raise TypeError(
            f"Cannot inspect endpoint {get_name(endpoint)!r}; use a typed function, bound method, callable instance or functools.partial"
        ) from None
    return signature, hints, source


def _adapt_endpoint(
    endpoint: Callable[..., Any],
    signature: inspect.Signature,
    hints: dict[str, Any],
    source: Callable[..., Any],
) -> Callable[..., Any]:
    if any(
        parameter.kind
        in (Parameter.POSITIONAL_ONLY, Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD)
        for parameter in signature.parameters.values()
    ):
        raise TypeError(
            f"Endpoint {get_name(endpoint)!r} needs named parameters; bind positional-only arguments before registration"
        )
    if inspect.isasyncgenfunction(source):

        async def generator(*args: Any, **kwargs: Any) -> Any:
            async with aclosing(endpoint(*args, **kwargs)) as iterator:
                async for item in iterator:
                    yield item

        adapted: Callable[..., Any] = generator
    elif inspect.iscoroutinefunction(source):

        async def coroutine(*args: Any, **kwargs: Any) -> Any:
            return await endpoint(*args, **kwargs)

        adapted = coroutine
    elif inspect.isgeneratorfunction(source):

        def sync_generator(*args: Any, **kwargs: Any) -> Any:
            yield from endpoint(*args, **kwargs)

        adapted = sync_generator
    else:

        def function(*args: Any, **kwargs: Any) -> Any:
            return endpoint(*args, **kwargs)

        adapted = function
    functools.update_wrapper(adapted, endpoint)
    adapted.__name__ = get_name(endpoint)
    adapted.__qualname__ = getattr(endpoint, "__qualname__", adapted.__name__)
    parameters = [
        parameter.replace(annotation=hints.get(name, parameter.annotation))
        for name, parameter in signature.parameters.items()
    ]
    result = hints.get("return", signature.return_annotation)
    adapted.__annotations__ = {
        parameter.name: parameter.annotation
        for parameter in parameters
        if parameter.annotation is not Parameter.empty
    }
    if result is not Parameter.empty:
        adapted.__annotations__["return"] = result
    adapted.__dict__["__signature__"] = signature.replace(
        parameters=parameters, return_annotation=result
    )
    adapted.__dict__["__canon_declared_endpoint__"] = getattr(
        endpoint, "__canon_declared_endpoint__", endpoint
    )
    return adapted


def _inject_async[EndpointT: Callable[..., Any]](
    endpoint: EndpointT, parameters: Any
) -> EndpointT:
    # FastAPI supplies the request to only one parameter, so reuse an existing one.
    request_hints = get_type_hints(endpoint)
    request_name = next(
        (name for name in parameters if request_hints.get(name) is Request), None
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
    wrapper.__dict__["__signature__"] = inspect.signature(endpoint).replace(
        parameters=replaced
    )
    wrapper.__annotations__ = annotations
    wrapper.__dict__["__canon_http_dependency_roots__"] = endpoint_dependencies(
        endpoint
    )
    return cast(EndpointT, wrapper)


def _parse_dependency(parameter: Parameter, hint: Any) -> DependencyKey | None:
    dependency = injected_dependency(hint)
    if dependency is not None:
        return DependencyKey(dependency, DEFAULT_COMPONENT)
    return default_parse_dependency(parameter, hint)


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
