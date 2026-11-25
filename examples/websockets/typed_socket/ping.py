from typing import Literal

from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature, Inject
from fastapi_canon.integrations.websockets import RpcChannel, RpcSocket
from pydantic import BaseModel, ConfigDict


class Ping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ping"] = "ping"
    value: int


class Pong(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["pong"] = "pong"
    value: int


messages = RpcChannel(namespace="messages")
type PingSocket = RpcSocket[Ping, Pong]


@messages.method()
async def health() -> bool:
    return True


router = APIRouter(namespace="messages")
router.include_channel(messages, path="/rpc", name="rpc")


@router.socket("/messages/ping", name="ping")
async def ping(message: Ping) -> Pong:
    return Pong(value=message.value + 1)


@router.socket("/messages/session", name="session")
async def session(socket: Inject[PingSocket]) -> None:
    await socket.send(Pong(value=0))
    async for message in socket:
        await socket.send(Pong(value=message.value))
        await socket.send(Pong(value=message.value + 1))


app = Composition(features=[Feature(name="messages", routers=[router])]).build(
    FastAPI(title="Typed Socket")
)
