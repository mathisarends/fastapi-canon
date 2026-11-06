from typing import Annotated, ForwardRef, get_args

from fastapi_canon import Inject
from fastapi_canon.injection import injected_dependency
from fastapi_canon.integrations.mcp import Inject as McpInject
from fastapi_canon.integrations.websockets import Inject as RpcInject

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


def test_forward_references_keep_only_the_marker() -> None:
    dependency, *metadata = get_args(Inject["Later"])

    assert isinstance(dependency, ForwardRef)
    assert dependency.__forward_arg__ == "Later"
    assert len(metadata) == 1


class Later:
    pass
