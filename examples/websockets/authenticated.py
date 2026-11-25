import time
from dataclasses import dataclass

from dishka import Provider, Scope, from_context, provide
from fastapi import FastAPI, WebSocket
from fastapi_canon import APIRouter, Composition, Feature, Inject
from fastapi_canon.integrations.websockets import (
    RpcChannel,
    RpcConnectionExpiry,
    RpcErrorBinding,
    RpcRejection,
    WebSocketPolicy,
)
from fastapi_canon.integrations.websockets.adapters.dishka import Dishka


@dataclass(frozen=True)
class Login:
    user: str
    expires_at: float


class Unauthenticated(Exception):
    pass


class ForbiddenOrigin(Exception):
    pass


class Sessions:
    def __init__(self) -> None:
        # Demo only: replace this store with the application's session service.
        self.values = {"demo": Login("ada", time.time() + 3600)}


@dataclass
class RequestIdentity:
    login: Login


class Dependencies(Provider):
    login = from_context(Login, scope=Scope.SESSION)
    sessions = provide(Sessions, scope=Scope.APP)

    @provide(scope=Scope.REQUEST)
    def identity(self, login: Login) -> RequestIdentity:
        return RequestIdentity(login)


async def open_session(websocket: WebSocket, sessions: Inject[Sessions]) -> Login:
    if websocket.headers.get("origin") != "https://app.example.test":
        raise ForbiddenOrigin("Origin is not allowed")
    login = sessions.values.get(websocket.cookies.get("session", ""))
    if login is None or login.expires_at <= time.time():
        raise Unauthenticated("Session is missing or expired")
    return login


async def session_expiry(login: Inject[Login]) -> RpcConnectionExpiry:
    return RpcConnectionExpiry(login.expires_at)


def create_app() -> FastAPI:
    authenticated = WebSocketPolicy(
        context=session_expiry,
        hooks=(open_session,),
        resolver=Dishka(),
        rejects=(
            RpcErrorBinding(
                Unauthenticated, message=str, rejection=RpcRejection.UNAUTHORIZED
            ),
            RpcErrorBinding(
                ForbiddenOrigin, message=str, rejection=RpcRejection.FORBIDDEN
            ),
        ),
    )
    router = APIRouter(websocket_policy=authenticated)
    channel = RpcChannel(namespace="account")

    @channel.method()
    async def whoami(identity: Inject[RequestIdentity]) -> str:
        return identity.login.user

    router.include_channel(channel, path="/rpc")
    feature = Feature(name="account", routers=[router], providers=[Dependencies])
    return Composition(features=(feature,)).build(FastAPI())


app = create_app()
