# Canon and Dishka

Declare sessions with `@rtc.session(...)` on a `WebRtcRouter` in the feature's
`routers`. Canon
installs Dishka's middleware and manages the application container. The router's
registry and `raises` declarations describe admission errors, which are exported
as HTTP denial metadata in the WebRTC contract while the native session runtime
and signaling protocol stay unchanged. See [WebRTC admission errors](../../webrtc.md).

For typed browser denials, see the optional [HTTP ticket recipe](admission-tickets.md).
For remote peers, the [send-only example](../../../examples/webrtc/remote_video/README.md)
binds a separate engine and lease to each session, including refused sessions.

The session factory is a FastAPI dependency. `Inject[T]` and `FromDishka[T]`
parameters resolve automatically through Composition; no `@inject` decorator
is required. The adapter uses a WebSocket, never an HTTP-only Request.
Session services use `Scope.SESSION`, matching Dishka's
[WebSocket scope model](https://dishka.readthedocs.io/en/stable/integrations/fastapi.html#websockets).

```python
from collections.abc import AsyncIterator

from dishka import AsyncContainer, FromDishka, Provider, Scope, provide
from fastapi import FastAPI
from fastapi_canon import Composition, Feature, Inject
from fastapi_canon.integrations.webrtc import (
    DataChannelSpec,
    Peer,
    Session,
    WebRtcRouter,
)
from fastapi_canon.integrations.webrtc.engines.aiortc import AiortcEngine


class Transcript:
    def __init__(self) -> None:
        self.messages: list[str] = []


class MessageWork:
    def reply(self, text: str, transcript: Transcript) -> str:
        transcript.messages.append(text)
        return text.upper()


class Services(Provider):
    @provide(scope=Scope.SESSION)
    async def transcript(self) -> AsyncIterator[Transcript]:
        transcript = Transcript()
        yield transcript
        # Persist or release session resources here, after on_close.

    @provide(scope=Scope.REQUEST)
    async def work(self) -> AsyncIterator[MessageWork]:
        yield MessageWork()
        # Release this message's transaction or other short-lived resources.


rtc = WebRtcRouter(engine=AiortcEngine())
CHAT = DataChannelSpec("chat", receive_model=str, send_model=str)


@rtc.session("/chat", data_channels=[CHAT])
async def chat(
    transcript: Inject[Transcript], container: FromDishka[AsyncContainer]
) -> Session:
    async def on_message(peer: Peer, text: str) -> None:
        async with container(scope=Scope.REQUEST) as request:
            work = await request.get(MessageWork)
            await peer.send_message(CHAT, work.reply(text, transcript))

    return Session(on_message={CHAT: on_message})


app = Composition(
    features=(Feature(name="chat", routers=[rtc], providers=[Services]),)
).build(FastAPI())
```

Ordinary synchronous functions, coroutine functions, synchronous generators and
async generators can be factories. FastAPI resolves the injected services before
calling the factory or entering its generator; synchronous factories run in its
worker thread. A generator yields exactly one `Session`. Its teardown runs after
session cleanup and before Dishka closes SESSION resources. Factory admission
exceptions retain their pre-accept HTTP denial behavior. Existing explicit
Dishka `@inject` factories remain supported.

Required path/query/header parameters remain FastAPI inputs. Only path/query
inputs enter the browser signaling contract: browsers cannot set arbitrary
WebSocket headers. Injected services never become client inputs. Prefer an HTTP
ticket or the documented cookie/origin admission policy for browser credentials.

Preflight checks marker roots against the prepared graph through APP/SESSION
scope (`canon.providers.E004`), without executing the factory or providers. A
missing binding or REQUEST-only service rejects `build()` before installation.
External containers, arbitrary nested `Depends`/`Security` functions and roots
already converted into Dishka's synchronous `Depends` wrappers remain outside
this check. Explicit uninspectable Dishka wrappers are reported as skipped;
installed-app checks retain the HTTP snapshot profile.

Callbacks are called directly with the documented `Peer`, message, track or
close-code arguments. Capture session services in the factory's closures;
adding `FromDishka[...]` callback parameters does not inject them. For resources
per message, enter a new child `Scope.REQUEST` and resolve services explicitly,
as above. Await all work using those resources before leaving the child scope.

Accepted sessions call `on_close` while session services remain available.
Owned tracks and engine peers are then released before the session dependency
scope exits, including when the ASGI task is cancelled during cleanup. Canon
runs feature-router shutdown before closing feature resources and its
application container.

FastAPI authentication dependencies can deny admission before the factory runs.
Capacity and router-shutdown checks happen after factory resolution: if a
returned `Session` is refused, its owned `tracks` are stopped before dependency
teardown, without calling `on_close` or allocating an engine peer. Factories
must clean up resources they create if they raise before returning a `Session`.

The executable [integration tests](../../../tests/integration/integrations/webrtc/test_declarations_dishka.py)
cover HTTP admission denial, capacity rejection, disconnect, application
shutdown, cleanup order and two real aiortc data-channel messages with distinct
request scopes sharing one session service.
