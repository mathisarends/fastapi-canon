import asyncio
import inspect
from collections.abc import AsyncGenerator, Generator
from operator import attrgetter

import pytest
from fastapi.routing import APIRoute

from fastapi_canon import APIRouter, Inject
from fastapi_canon.injected_route import InjectedRoute, inject_endpoint


async def plain(value: int) -> int:
    return value


async def injected(value: int, service: Inject[str]) -> str:
    return f"{value}{service}"


def test_endpoints_without_injection_are_returned_unchanged() -> None:
    assert inject_endpoint(plain) is plain
    assert not hasattr(plain, "__dishka_injected__")


def test_injected_parameters_leave_the_public_signature() -> None:
    parameters = inspect.signature(inject_endpoint(injected)).parameters

    assert "service" not in parameters
    assert "value" in parameters
    assert "service" in inspect.signature(injected).parameters


def test_api_router_uses_injected_routes_unless_configured() -> None:
    assert APIRouter().route_class is InjectedRoute
    assert APIRouter(route_class=APIRoute).route_class is APIRoute


def test_opaque_callable_reports_a_targeted_setup_error() -> None:
    with pytest.raises(TypeError, match=r"(?i)endpoint 'attrgetter'"):
        inject_endpoint(attrgetter("name"))


def test_variadic_callable_reports_a_targeted_setup_error() -> None:
    class Variadic:
        def __call__(self, *services: Inject[str]) -> str:
            return "value"

    with pytest.raises(TypeError, match="needs named parameters"):
        inject_endpoint(Variadic())


def test_endpoint_constructors_need_an_explicit_typed_adapter() -> None:
    calls: list[str] = []

    class Constructor:
        def __init__(self, service: Inject[str]) -> None:
            calls.append(service)

    with pytest.raises(TypeError, match="Endpoint constructors with injection"):
        inject_endpoint(Constructor)
    assert calls == []


def test_async_generator_callable_closes_its_source_on_early_exit() -> None:
    class Events:
        closed = False

        async def __call__(self) -> AsyncGenerator[str, None]:
            try:
                yield "first"
                yield "second"
            finally:
                self.closed = True

    endpoint = Events()
    adapted = inject_endpoint(endpoint)
    assert not endpoint.closed

    async def scenario() -> None:
        async with asyncio.timeout(5):
            stream = adapted()
            assert await anext(stream) == "first"
            await stream.aclose()

    asyncio.run(scenario())
    assert endpoint.closed


def test_sync_generator_callable_closes_its_source_on_early_exit() -> None:
    class Events:
        closed = False

        def __call__(self) -> Generator[str, None, None]:
            try:
                yield "first"
                yield "second"
            finally:
                self.closed = True

    endpoint = Events()
    adapted = inject_endpoint(endpoint)
    assert not endpoint.closed
    stream = adapted()
    assert next(stream) == "first"
    stream.close()
    assert endpoint.closed
