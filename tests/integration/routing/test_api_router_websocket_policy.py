from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

import pytest
from fastapi import APIRouter as FastAPIRouter
from fastapi import Depends, FastAPI, Header, WebSocket
from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute
from starlette.testclient import WebSocketDenialResponse, WebSocketTestSession

from fastapi_canon import APIRouter, Composition, Error, Feature, Inject
from fastapi_canon.errors import ConfigurationError
from fastapi_canon.integrations.websockets import (
    ProtocolDefinitionError,
    RpcChannel,
    RpcConnectionScope,
    RpcErrorBinding,
    RpcMiddleware,
    RpcRejection,
    RpcSocket,
    WebSocketPolicy,
)


@dataclass(frozen=True)
class User:
    name: str


class Unauthorized(Exception):
    pass


class Unknown(Exception):
    pass


UNAUTHORIZED = RpcErrorBinding(Unauthorized, rejection=RpcRejection.UNAUTHORIZED)
UNKNOWN = RpcErrorBinding(Unknown, rejection=RpcRejection.NOT_FOUND)


async def authenticate(
    token: Annotated[str | None, Header(alias="X-User")] = None,
) -> User:
    if token is None:
        raise Unauthorized("Sign in")
    if token == "unknown":
        raise Unknown("Unknown user")
    return User(token)


@dataclass
class Recorder(RpcMiddleware):
    label: str
    events: list[str]

    @asynccontextmanager
    async def connection(self, scope: RpcConnectionScope) -> AsyncGenerator[None]:
        self.events.append(f"{self.label}:open")
        try:
            yield
        finally:
            self.events.append(f"{self.label}:close")


AUTHENTICATED = WebSocketPolicy(context=authenticate, rejects=[UNAUTHORIZED])


@pytest.mark.parametrize("kind", ["channel", "socket", "binary_stream"])
def test_policy_admits_each_connection_before_accepting_it(kind: str) -> None:
    events: list[str] = []
    policy = AUTHENTICATED.extend(middleware=[Recorder("policy", events)])
    router = APIRouter(prefix="/me", namespace="me", websocket_policy=policy)
    endpoint = {"middleware": [Recorder("endpoint", events)]}
    if kind == "channel":
        session = RpcChannel(namespace="me")

        @session.method()
        async def name(user: Inject[User]) -> str:
            return user.name

        router.include_channel(session, path="/session", **endpoint)

    elif kind == "socket":

        @router.socket("/session", **endpoint)
        async def echo(socket: Inject[RpcSocket], user: Inject[User]) -> None:
            await socket.send(user.name)

    else:

        @router.binary_stream("/session", **endpoint)
        async def frames(user: Inject[User]) -> AsyncIterator[bytes]:
            yield user.name.encode()

    app = Composition(features=[Feature(name="me", routers=[router])]).build(FastAPI())

    with TestClient(app) as client:
        with (
            pytest.raises(WebSocketDenialResponse) as denied,
            client.websocket_connect("/me/session"),
        ):
            pass
        assert denied.value.status_code == 401
        assert events == [
            "policy:open",
            "endpoint:open",
            "endpoint:close",
            "policy:close",
        ]
        with (
            client.websocket_connect("/me/session", headers={"X-User": "ada"}) as ada,
            client.websocket_connect("/me/session", headers={"X-User": "bob"}) as bob,
        ):
            assert [_receive(kind, ada), _receive(kind, bob)] == ["ada", "bob"]


def test_one_error_declaration_answers_http_and_rejects_the_handshake() -> None:
    sign_in_required = Error(
        Unauthorized, status=401, code="sign_in_required", title="Sign in first"
    )
    router = APIRouter(
        prefix="/me",
        websocket_policy=WebSocketPolicy(
            context=authenticate, rejects=[sign_in_required]
        ),
    )

    @router.get(
        "/name", dependencies=[Depends(authenticate)], raises=[sign_in_required]
    )
    async def name() -> str:
        return "ada"

    @router.binary_stream("/session")
    async def frames(user: Inject[User]) -> AsyncIterator[bytes]:
        yield user.name.encode()

    app = Composition(features=[Feature(name="me", routers=[router])]).build(FastAPI())

    with TestClient(app) as client:
        problem = client.get("/me/name")
        with (
            pytest.raises(WebSocketDenialResponse) as denied,
            client.websocket_connect("/me/session"),
        ):
            pass

    assert (problem.status_code, problem.json()["code"]) == (401, "sign_in_required")
    assert (denied.value.status_code, denied.value.text) == (401, "Sign in first")


def test_error_status_without_a_rejection_is_refused_at_declaration() -> None:
    conflict = Error(Unknown, status=409, code="user_conflict")

    with pytest.raises(ProtocolDefinitionError, match="status 409"):
        APIRouter(websocket_policy=WebSocketPolicy(rejects=[conflict])).binary_stream(
            "/session"
        )(_frames)


async def _frames() -> AsyncIterator[bytes]:
    yield b""


@pytest.mark.parametrize("native_wrapper", [False, True])
def test_routers_with_an_extended_policy_can_be_included(native_wrapper: bool) -> None:
    parent = APIRouter(websocket_policy=AUTHENTICATED)
    child = APIRouter(
        prefix="/users", websocket_policy=AUTHENTICATED.extend(rejects=[UNKNOWN])
    )

    @child.binary_stream("/me")
    async def me(user: Inject[User]) -> AsyncIterator[bytes]:
        yield user.name.encode()

    if native_wrapper:
        wrapper = FastAPIRouter()
        wrapper.include_router(child)
        parent.include_router(wrapper)
    else:
        parent.include_router(child)
    app = Composition(features=[Feature(name="users", routers=[parent])]).build(
        FastAPI()
    )

    with TestClient(app) as client:
        for headers, status in (({}, 401), ({"X-User": "unknown"}, 404)):
            with (
                pytest.raises(WebSocketDenialResponse) as denied,
                client.websocket_connect("/users/me", headers=headers),
            ):
                pass
            assert denied.value.status_code == status


async def anonymous(user: Inject[RpcSocket]) -> None:
    await user.close()


async def native(websocket: WebSocket) -> None:
    await websocket.close()


@pytest.mark.parametrize("child", ["canon", "fastapi"])
def test_policy_routers_reject_included_routes_without_the_policy(child: str) -> None:
    parent = APIRouter(websocket_policy=AUTHENTICATED)
    included: FastAPIRouter
    if child == "canon":
        canon = APIRouter(websocket_policy=WebSocketPolicy(context=authenticate))
        canon.socket("/anonymous")(anonymous)
        included = canon
    else:
        included = FastAPIRouter()
        included.add_api_websocket_route("/native", native)

    with pytest.raises(ConfigurationError, match="websocket_policy"):
        parent.include_router(included)
    assert parent.routes == []


def test_policy_routers_reject_their_own_native_websockets() -> None:
    router = APIRouter(websocket_policy=AUTHENTICATED)

    with pytest.raises(ConfigurationError, match="websocket_policy"):
        router.websocket("/native")(native)


def test_policy_routers_reject_starlette_websocket_registration() -> None:
    router = APIRouter(websocket_policy=AUTHENTICATED)

    with pytest.raises(ConfigurationError, match="websocket_policy"):
        router.add_websocket_route("/native", native)
    assert router.routes == []


def test_unprotected_constructor_routes_cannot_be_installed() -> None:
    router = APIRouter(
        websocket_policy=AUTHENTICATED,
        routes=[WebSocketRoute("/native", native)],
    )
    app = FastAPI()
    routes = tuple(app.routes)
    with pytest.raises(ConfigurationError, match="websocket_policy"):
        Composition(features=[Feature(name="users", routers=[router])]).build(app)
    assert tuple(app.routes) == routes


def test_native_child_routes_added_after_startup_cannot_bypass_the_policy() -> None:
    parent = APIRouter(websocket_policy=AUTHENTICATED)
    parent.socket("/protected")(anonymous)
    child = FastAPIRouter()
    parent.include_router(child)
    app = Composition(features=[Feature(name="users", routers=[parent])]).build(
        FastAPI()
    )
    calls: list[str] = []

    async def late(websocket: WebSocket) -> None:
        calls.append("unauthenticated")
        await websocket.accept()
        await websocket.close()

    with TestClient(app) as client:
        # Warm FastAPI's included-router cache before the child changes.
        with client.websocket_connect("/protected", headers={"X-User": "ada"}):
            pass
        child.add_api_websocket_route("/late", late)
        with (
            pytest.raises(ConfigurationError, match="websocket_policy"),
            client.websocket_connect("/late"),
        ):
            pass
    assert calls == []


def test_included_routers_cannot_add_routes_without_the_policy_later() -> None:
    parent = APIRouter(websocket_policy=AUTHENTICATED)
    child = APIRouter()
    grandchild = APIRouter()
    child.include_router(grandchild)
    parent.include_router(child)

    with pytest.raises(ConfigurationError, match="websocket_policy"):
        grandchild.socket("/anonymous")(anonymous)


def _receive(kind: str, socket: WebSocketTestSession) -> str:
    if kind == "channel":
        socket.send_json({"jsonrpc": "2.0", "id": 1, "method": "me.name"})
        return str(socket.receive_json()["result"])
    if kind == "socket":
        return str(socket.receive_text())
    return bytes(socket.receive_bytes()).decode()
