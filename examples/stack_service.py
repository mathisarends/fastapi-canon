from collections.abc import AsyncIterator
from enum import StrEnum
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


class IncrementCounter:
    def __init__(self, counter: Counter) -> None:
        self._counter = counter

    def __call__(self) -> int:
        self._counter.value += 1
        return self._counter.value


class StackProvider(Provider):
    @provide(scope=Scope.APP)
    async def counter(self) -> AsyncIterator[Counter]:
        counter = Counter()
        yield counter
        counter.closed = True

    @provide(scope=Scope.REQUEST)
    async def increment_counter(
        self, counter: Counter
    ) -> AsyncIterator[IncrementCounter]:
        yield IncrementCounter(counter)
        counter.calls_closed += 1


class CounterEventType(StrEnum):
    COUNTER_CHANGED = "counter.changed"


class CounterEvent(BaseModel):
    type: Literal[CounterEventType.COUNTER_CHANGED] = CounterEventType.COUNTER_CHANGED
    value: int


class IncrementResult(BaseModel):
    value: int


def create_app() -> FastAPI:
    router = APIRouter()
    channel = RpcChannel("counter")

    @router.post("/counter", operation_id="increment_counter")
    async def increment_http(
        increment_counter: Inject[IncrementCounter],
    ) -> IncrementResult:
        return IncrementResult(value=increment_counter())

    @router.sse("/counter/events", operation_id="counter_events")
    async def events(counter: Inject[Counter]) -> AsyncIterator[CounterEvent]:
        yield CounterEvent(value=counter.value)

    @channel.method("increment")
    async def increment_rpc(
        increment_counter: Inject[IncrementCounter],
    ) -> IncrementResult:
        return IncrementResult(value=increment_counter())

    router.include_channel(channel, path="/rpc")
    feature = Feature(name="counter", routers=[router], providers=[StackProvider])
    return Composition(features=(feature,)).build(FastAPI(title="Canon stack example"))


app = create_app()
