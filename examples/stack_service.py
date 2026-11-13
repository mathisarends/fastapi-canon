from collections.abc import AsyncIterator
from typing import Literal

from dishka import Provider, Scope, provide
from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_canon import APIRouter, Composition, Feature, Inject
from fastapi_canon.integrations.websockets import RpcChannel

__all__ = [
    "app",
    "create_app",
]


class Counter:
    def __init__(self) -> None:
        self.value = 0
        self.closed = False
        self.calls_closed = 0


class Operations:
    def __init__(self, counter: Counter) -> None:
        self._counter = counter

    def increment(self) -> int:
        self._counter.value += 1
        return self._counter.value


class StackProvider(Provider):
    @provide(scope=Scope.APP)
    async def counter(self) -> AsyncIterator[Counter]:
        counter = Counter()
        yield counter
        counter.closed = True

    @provide(scope=Scope.REQUEST)
    async def operations(self, counter: Counter) -> AsyncIterator[Operations]:
        yield Operations(counter)
        counter.calls_closed += 1


class CounterEvent(BaseModel):
    type: Literal["counter.changed"] = "counter.changed"
    value: int


class IncrementResult(BaseModel):
    value: int


def create_app() -> FastAPI:
    router = APIRouter()
    channel = RpcChannel("counter")

    @router.post("/counter", operation_id="increment_counter")
    async def increment_http(operations: Inject[Operations]) -> IncrementResult:
        return IncrementResult(value=operations.increment())

    @router.sse("/counter/events", operation_id="counter_events")
    async def events(counter: Inject[Counter]) -> AsyncIterator[CounterEvent]:
        yield CounterEvent(value=counter.value)

    @channel.method("increment")
    async def increment_rpc(operations: Inject[Operations]) -> IncrementResult:
        return IncrementResult(value=operations.increment())

    router.include_channel(channel, path="/rpc")
    feature = Feature(name="counter", routers=[router], providers=[StackProvider])
    return Composition(features=(feature,)).build(FastAPI(title="Canon stack example"))


app = create_app()
