from typing import Annotated, get_args, get_type_hints

from fastapi_canon import Inject
from fastapi_canon.integrations.mcp import Inject as McpInject
from fastapi_canon.integrations.websockets import Inject as RpcInject
from fastapi_canon.routing.injection import (
    injected_dependency,
    injected_resolver_dependency,
)

type Service = Inject[str]
type WrappedService = Annotated[Service, "other metadata"]


def test_injection_is_shared_across_transports() -> None:
    assert Inject is McpInject is RpcInject


def test_aliases_keep_the_dependency_type() -> None:
    assert injected_dependency(WrappedService) is str


def test_unmarked_annotations_are_not_injected() -> None:
    assert injected_dependency(Annotated[str, "other metadata"]) is None


def test_injection_annotations_are_stable_per_dependency() -> None:
    assert Inject[int] == Inject[int]
    assert injected_dependency(Inject[int]) is int


def forward_reference(service: Inject["Later"]) -> None:
    pass


def test_forward_references_resolve_the_marker_and_fastapi_dependency() -> None:
    hint = get_type_hints(
        forward_reference, globalns={"Later": Later}, include_extras=True
    )["service"]
    _, _, dependency = get_args(hint)

    assert injected_dependency(hint) is Later
    assert injected_resolver_dependency(dependency.dependency) is Later


class Later:
    pass
