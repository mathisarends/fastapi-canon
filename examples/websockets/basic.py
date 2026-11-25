from dataclasses import dataclass

from dishka import Provider, Scope
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_canon import APIRouter, Composition, Feature, Inject
from fastapi_canon.integrations.websockets import RpcChannel
from pydantic import BaseModel


class Greeting(BaseModel):
    text: str


@dataclass(frozen=True)
class Greeter:
    salutation: str


greeting = RpcChannel(namespace="greeting")


@greeting.method()
async def say(name: str, greeter: Inject[Greeter]) -> Greeting:
    return Greeting(text=f"{greeter.salutation}, {name}!")


router = APIRouter()
router.include_channel(greeting, path="/rpc")
providers = Provider()
providers.provide(lambda: Greeter("Hello"), provides=Greeter, scope=Scope.APP)
app = Composition(
    features=[Feature(name="greeting", routers=[router], providers=[providers])]
).build(FastAPI())


def main() -> None:
    with TestClient(app) as client, client.websocket_connect("/rpc") as socket:
        socket.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "greeting.say",
                "params": {"name": "World"},
            }
        )
        print(socket.receive_json())


if __name__ == "__main__":
    main()
