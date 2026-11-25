from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi_canon.integrations.websockets import (
    ProtocolDefinitionError,
    RpcChannel,
    RpcServer,
)
from pydantic import BaseModel


class Greeting(BaseModel):
    text: str


def _request(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


async def test_named_parameters_are_the_params_object() -> None:
    channel = RpcChannel(namespace="greeting")

    @channel.method()
    async def say(name: str, times: int = 1) -> str:
        return " ".join([f"Hello, {name}"] * times)

    server = RpcServer.from_channel(channel.freeze())
    assert (await server.handle(_request("greeting.say", {"name": "Ada"}))).result == (
        "Hello, Ada"
    )
    repeated = await server.handle(
        _request("greeting.say", {"name": "Ada", "times": 2})
    )
    assert repeated.result == "Hello, Ada Hello, Ada"
    rejected = await server.handle(_request("greeting.say", {"name": "Ada", "x": 1}))
    assert rejected.error.code == -32602
    params = channel.freeze().methods[0].params
    assert params is not None
    assert params.__name__ == "GreetingSayParams"


async def test_a_single_model_parameter_is_the_whole_params_object() -> None:
    channel = RpcChannel(namespace="greeting")

    @channel.method()
    async def echo(greeting: Greeting) -> Greeting:
        return greeting

    @channel.method()
    async def wrap(greeting: Greeting, loud: bool = False) -> str:
        return greeting.text.upper() if loud else greeting.text

    server = RpcServer.from_channel(channel.freeze())
    echoed = await server.handle(_request("greeting.echo", {"text": "hi"}))
    assert echoed.result == Greeting(text="hi")
    wrapped = await server.handle(
        _request("greeting.wrap", {"greeting": {"text": "hi"}, "loud": True})
    )
    assert wrapped.result == "HI"


async def test_message_streams_take_named_parameters() -> None:
    channel = RpcChannel(namespace="counter")

    @channel.stream()
    async def count(limit: int) -> AsyncIterator[int]:
        for value in range(limit):
            yield value

    stream = channel.freeze().subscriptions[0]
    assert stream.params is not None
    assert set(stream.params.model_json_schema()["properties"]) == {"limit"}
    assert stream.params_fields == ("limit",)


def test_parameters_must_be_passable_by_name() -> None:
    channel = RpcChannel(namespace="greeting")

    @channel.method()
    async def say(name: str, /) -> str:
        return name

    with pytest.raises(ProtocolDefinitionError, match="passable by name"):
        channel.freeze()
