from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi_canon import Inject
from fastapi_canon.integrations.websockets import CanonWebSocketRouter


@dataclass
class Access:
    pass


@dataclass
class Other:
    pass


async def context(access: Inject[Access]) -> Other:
    return Other()


def test_duplicate_hook_results_fail_during_registration() -> None:
    async def first() -> Access:
        return Access()

    async def second() -> Access:
        return Access()

    with pytest.raises(TypeError, match="Multiple context hooks provide Access"):
        CanonWebSocketRouter(context=context, hooks=(first, second))


def test_hook_cycles_fail_during_registration() -> None:
    async def first(other: Inject[Other]) -> Access:
        return Access()

    async def second(access: Inject[Access]) -> Other:
        return Other()

    with pytest.raises(TypeError, match="Cyclic context hooks"):
        CanonWebSocketRouter(context=context, hooks=(first, second))


def test_hooks_require_a_context() -> None:
    with pytest.raises(ValueError, match="hooks= requires a context="):
        CanonWebSocketRouter(hooks=(context,))


def test_generator_resources_belong_in_providers() -> None:
    async def resource() -> AsyncIterator[Access]:
        yield Access()

    with pytest.raises(TypeError, match="functions returning a concrete value"):
        CanonWebSocketRouter(context=context, hooks=(resource,))
