import inspect
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from contextlib import aclosing
from functools import wraps
from typing import Any, cast, get_args, get_type_hints

from fastapi import Request
from pydantic import TypeAdapter
from starlette.concurrency import iterate_in_threadpool
from starlette.routing import Match

from fastapi_canon.contract_routes import iter_contract_routes
from fastapi_canon.error import ErrorConfigurationError, ErrorRegistry
from fastapi_canon.error.catalog import app_registry, internal_problem, problem_for
from fastapi_canon.error.openapi import inherited_contract
from fastapi_canon.error.registry import AnyError
from fastapi_canon.integrations.sse.router import (
    ServerSentEvent,
    StreamRouter,
    validate_endpoint,
)
from fastapi_canon.policy import normalize_raises
from fastapi_canon.router import CanonRouter

__all__ = [
    "CanonStreamRouter",
]

logger = logging.getLogger(__name__)


class CanonStreamRouter(CanonRouter, StreamRouter):
    """Typed SSE with Canon HTTP admission and terminal problem contracts."""

    def stream[Endpoint: Callable[..., Any]](
        self,
        path: str,
        *,
        methods: list[str] | None = None,
        raises: Sequence[AnyError] = (),
        **options: Any,
    ) -> Callable[[Endpoint], Endpoint]:
        """Declare admission and terminal processing failures with raises.

        Normal exhaustion sends canon.complete. Processing failures send a terminal
        canon.problem. Disconnects and cancellation propagate through cleanup.
        """
        local = normalize_raises(raises, parameter="raises")
        errors = tuple(dict.fromkeys((*self.raises, *local)))
        self._register_errors(errors)
        registry = self.error_registry
        extra = dict(options.pop("openapi_extra", None) or {})
        if "x-canon-stream" in extra:
            raise ErrorConfigurationError(
                "x-canon-stream is owned by CanonStreamRouter"
            )
        extra["x-canon-stream"] = {
            "version": 1,
            "errorIds": [str(id(error)) for error in errors],
        }
        options["openapi_extra"] = extra

        register = super().stream(path, methods=methods, raises=local, **options)

        def decorate(endpoint: Endpoint) -> Endpoint:
            validate_endpoint(endpoint)
            wrapped = _terminal_stream(endpoint, registry, errors)
            register(wrapped)
            return endpoint

        return decorate


def _terminal_stream(
    endpoint: Callable[..., Any],
    registry: ErrorRegistry | None,
    errors: tuple[AnyError, ...],
) -> Callable[..., Any]:
    hints = get_type_hints(endpoint, include_extras=True)
    item_type = get_args(hints["return"])[0]
    adapter = TypeAdapter(item_type)
    signature = inspect.signature(endpoint)
    request_hints = get_type_hints(endpoint)
    existing_request = next(
        (
            p.name
            for p in signature.parameters.values()
            if request_hints.get(p.name, p.annotation) is Request
        ),
        None,
    )
    request_name = existing_request or "_canon_request"
    if existing_request is None:
        while request_name in signature.parameters:
            request_name += "_"

    @wraps(endpoint)
    async def wrapped(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        request: Request = kwargs[request_name]
        if existing_request is None:
            kwargs.pop(request_name)
        catalog = app_registry(
            request.app, registry if registry is not None else ErrorRegistry(errors=[])
        )
        source = endpoint(*args, **kwargs)
        iterator = (
            source
            if inspect.isasyncgen(source)
            else cast(AsyncGenerator[Any, None], iterate_in_threadpool(source))
        )
        try:
            async with aclosing(iterator):
                async for item in iterator:
                    if isinstance(item, ServerSentEvent):
                        if item.event and item.event.startswith("canon."):
                            raise ValueError("canon.* SSE event names are reserved")
                        if (
                            item.raw_data is not None
                            or item.comment is not None
                            and item.data is None
                        ):
                            raise ValueError(
                                "Canon streams require typed JSON business events"
                            )
                        value = adapter.validate_python(item.data)
                        yield item.model_copy(
                            update={
                                "data": None,
                                "raw_data": adapter.dump_json(
                                    value, by_alias=True
                                ).decode("utf-8"),
                            }
                        )
                    else:
                        value = adapter.validate_python(item)
                        yield ServerSentEvent(
                            raw_data=adapter.dump_json(value, by_alias=True).decode(
                                "utf-8"
                            )
                        )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # This boundary handles arbitrary application generator exceptions.
            declared = catalog.resolve(exc)
            effective_errors = (*errors, *_inherited_stream_errors(request))
            try:
                problem = (
                    problem_for(catalog, exc)
                    if declared is not None
                    and any(declared is e for e in effective_errors)
                    else None
                )
            except Exception:  # pylint: disable=broad-exception-caught
                # Public renderers are application callbacks and may fail arbitrarily.
                logger.exception("SSE problem renderer failed")
                problem = None
            if problem is None:
                logger.error("SSE operation %s failed", endpoint.__name__, exc_info=exc)
                problem = internal_problem(catalog)
            yield ServerSentEvent(event="canon.problem", data=problem.as_dict())
            return
        finally:
            if inspect.isgenerator(source):
                source.close()
        yield ServerSentEvent(event="canon.complete", data={})

    parameters = [
        p.replace(annotation=hints.get(p.name, p.annotation))
        for p in signature.parameters.values()
    ]
    position = next(
        (
            i
            for i, p in enumerate(parameters)
            if p.kind == inspect.Parameter.VAR_KEYWORD
        ),
        len(parameters),
    )
    if existing_request is None:
        parameters.insert(
            position,
            inspect.Parameter(
                request_name, inspect.Parameter.KEYWORD_ONLY, annotation=Request
            ),
        )
    wrapped.__annotations__ = {
        **{
            p.name: p.annotation
            for p in parameters
            if p.annotation is not inspect.Parameter.empty
        },
        "return": AsyncIterator[item_type],
    }
    cast(Any, wrapped).__signature__ = signature.replace(
        parameters=parameters, return_annotation=AsyncIterator[item_type]
    )
    wrapped.__dict__["__canon_declared_endpoint__"] = getattr(
        endpoint, "__canon_declared_endpoint__", endpoint
    )
    return wrapped


def _inherited_stream_errors(request: Request) -> tuple[AnyError, ...]:
    # Lazy FastAPI inclusion keeps parent callbacks on the effective route,
    # while the request scope may still expose only the original child route.
    for _, route in iter_contract_routes(request.app.routes, include_mounts=False):
        match, _ = route.matches(request.scope)
        if match is Match.FULL:
            errors, _ = inherited_contract(route)
            return errors
    return ()
