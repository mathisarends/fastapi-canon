from collections.abc import AsyncIterator
from typing import assert_type

import pytest
from dishka import Provider, Scope, make_async_container

from fastapi_canon import Scoped


class SyncUseCase:
    def __call__(self) -> int:
        return 1


class GeneratorUseCase:
    async def __call__(self) -> AsyncIterator[int]:
        yield 1


@pytest.mark.parametrize("use_case", [SyncUseCase, GeneratorUseCase, object])
async def test_scoped_rejects_non_coroutine_use_cases(use_case: type[object]) -> None:
    async with make_async_container() as container:
        with pytest.raises(TypeError, match="async __call__"):
            Scoped(container, use_case)


async def test_scoped_requires_app_container() -> None:
    class UseCase:
        async def __call__(self) -> int:
            return 1

    async with (
        make_async_container() as container,
        container(scope=Scope.REQUEST) as child,
    ):
        with pytest.raises(ValueError, match="APP container"):
            Scoped(child, UseCase)


async def test_scoped_preserves_callable_signature() -> None:
    class UseCase:
        async def __call__(self, number: int, /, *, suffix: str) -> str:
            return f"{number}{suffix}"

    provider = Provider()
    provider.provide(UseCase, scope=Scope.REQUEST)
    async with make_async_container(provider) as container:
        scoped = Scoped(container, UseCase)
        result = await scoped(3, suffix="!")
        assert_type(result, str)
        assert result == "3!"


async def test_scoped_resolves_inherited_callable_from_named_component() -> None:
    class Base:
        async def __call__(self, value: str) -> str:
            return value.upper()

    class UseCase(Base):
        pass

    provider = Provider(component="named")
    provider.provide(UseCase, scope=Scope.REQUEST)
    async with make_async_container(provider) as container:
        scoped = Scoped(container, UseCase, component="named")
        assert await scoped("canon") == "CANON"
