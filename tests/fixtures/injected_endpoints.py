"""Future-annotated endpoint forms shared by request and contract scenarios."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from dishka import FromDishka
from pydantic import BaseModel

from fastapi_canon import Inject


class EndpointStore:
    def __init__(self, identifier: int) -> None:
        self.identifier = identifier


class AsyncReader:
    __slots__ = ("prefix",)

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    async def __call__(
        self, store: Inject[EndpointStore], same: FromDishka[EndpointStore]
    ) -> str:
        return f"{self.prefix}:{store.identifier}:{store is same}"


class SyncReader:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def __call__(
        self, store: Inject[EndpointStore], same: FromDishka[EndpointStore]
    ) -> str:
        return f"{self.prefix}:{store.identifier}:{store is same}"


class PositionalReader:
    async def __call__(
        self,
        prefix: str,
        /,
        store: Inject[EndpointStore],
        same: FromDishka[EndpointStore],
    ) -> str:
        return f"{prefix}:{store.identifier}:{store is same}"


class BoundReader:
    Service = EndpointStore

    async def read(self, store: Inject[Service], same: FromDishka[Service]) -> str:
        return f"bound:{store.identifier}:{store is same}"

    def sync_read(self, store: Inject[Service], same: FromDishka[Service]) -> str:
        return f"bound-sync:{store.identifier}:{store is same}"


class ReaderTemplate:
    Service = EndpointStore

    async def __call__(self, store: Inject[Service], same: FromDishka[Service]) -> str:
        return f"inherited:{store.identifier}:{store is same}"


class InheritedReader(ReaderTemplate):
    pass


class PublicPayload(BaseModel):
    value: int


class AsyncEcho:
    async def __call__(self, payload: PublicPayload) -> PublicPayload:
        return payload


class SyncEcho:
    def __call__(self, payload: PublicPayload) -> PublicPayload:
        return payload


async def async_read(
    prefix: str, store: Inject[EndpointStore], same: FromDishka[EndpointStore]
) -> str:
    return f"{prefix}:{store.identifier}:{store is same}"


def sync_read(
    prefix: str, store: Inject[EndpointStore], same: FromDishka[EndpointStore]
) -> str:
    return f"{prefix}:{store.identifier}:{store is same}"


def reader(kind: str) -> Callable[..., Any]:
    match kind:
        case "async-object":
            return AsyncReader("async")
        case "sync-object":
            return SyncReader("sync")
        case "bound-async":
            return BoundReader().read
        case "bound-sync":
            return BoundReader().sync_read
        case "inherited":
            return InheritedReader()
        case "partial-async":
            return partial(async_read, "partial-async")
        case "partial-sync":
            return partial(sync_read, "partial-sync")
        case "keyword-async":
            return partial(async_read, prefix="keyword-async")
        case "keyword-sync":
            return partial(sync_read, prefix="keyword-sync")
        case "bound-positional":
            return partial(PositionalReader(), "bound-positional")
        case _:
            raise ValueError(kind)
