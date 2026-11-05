import inspect

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
