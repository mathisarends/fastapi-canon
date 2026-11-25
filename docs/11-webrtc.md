# 11. WebRTC

Real-time audio, video and typed data channels, for example voice agents.
Sessions are declared like routes; a typed browser client is generated.
Install `fastapi-canon[webrtc,aiortc]`.

## A session

```python
from typing import Annotated

from aiortc.contrib.media import MediaRelay
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

from fastapi_canon import Composition, Feature
from fastapi_canon.integrations.webrtc import (
    DataChannelSpec,
    Direction,
    IncomingTrack,
    MediaPolicy,
    Peer,
    Session,
    WebRtcRouter,
)
from fastapi_canon.integrations.webrtc.engines.aiortc import AiortcEngine


class Prompt(BaseModel):
    text: str


class Feedback(BaseModel):
    text: str
    words: int


# The spec types the channel handles: `peer.channel(MESSAGES)` receives
# `Prompt` and sends `Feedback`.
MESSAGES = DataChannelSpec("messages", receive_model=Prompt, send_model=Feedback)


def lesson_access(lesson_id: str, token: Annotated[str, Query()]) -> str:
    if token != "demo":
        raise HTTPException(status_code=403)  # HTTP 403; no peer is created.
    return lesson_id


rtc = WebRtcRouter(prefix="/rtc", engine=AiortcEngine())


@rtc.session(
    "/lessons/{lesson_id}",
    name="practice",
    media=MediaPolicy(audio=Direction.SEND_RECEIVE),
    data_channels=(MESSAGES,),
    max_sessions=50,
)
def practice(lesson: Annotated[str, Depends(lesson_access)]) -> Session:
    relay = MediaRelay()

    async def on_track(peer: Peer, track: IncomingTrack) -> None:
        await peer.send_track(relay.subscribe(track.native))  # Echo the microphone.

    async def on_prompt(peer: Peer, prompt: Prompt) -> None:
        await peer.send_message(
            MESSAGES, Feedback(text=prompt.text, words=len(prompt.text.split()))
        )

    return Session(on_track=on_track, on_message={MESSAGES: on_prompt})


app = Composition(features=[Feature(name="lessons", routers=[rtc])]).build(FastAPI())
```

The session factory is a FastAPI dependency chain: authentication and path or
query parameters run before any peer connection exists. A `Session` is built
from handlers:

| Handler | Runs |
| --- | --- |
| `on_start(peer)` | once connected and channels are open |
| `run(peer)` | as the main task; returning closes the session |
| `on_track(peer, track)` | for each incoming track |
| `on_message={spec: handler}` | for each validated data-channel message |
| `on_close(peer, code)` | before resources are released |

## Errors on admission

Declare admission failures like on any route:

```python
rtc = WebRtcRouter(engine=AiortcEngine(), raises=[browser_missing])
```

Before the WebSocket is accepted, a mapped exception refuses the session with
its HTTP status and no peer is created. After that, failures use WebRTC close
codes.

## Browser client

```console
fastapi-canon webrtc contract --app app:app --output contract.json
fastapi-canon webrtc generate --contract contract.json --output src/rtc --client-name PracticeRtcClient
```

```ts
import { PracticeRtcClient, isRtcError } from "./rtc/index.js";

const client = new PracticeRtcClient({ baseUrl: "https://api.example.com" });
const microphone = await navigator.mediaDevices.getUserMedia({ audio: true });

const session = await client.practice.connect({
  lessonId: "greetings", // path parameter
  token: "demo", // query parameter
  tracks: microphone, // borrowed: never stopped by the session
  timeoutMs: 10_000,
  onTrack: ({ track }) => {
    audio.srcObject = new MediaStream([track]);
  },
  onMessage: { messages: (feedback) => console.log(feedback.words) },
});

await session.channels.messages.send({ text: "hello" }); // typed as Prompt
await session.close(); // idempotent
```

Failures are `RtcError`s with a stable `code` such as `session_limit` or
`transport_lost`; see the [close codes](integrations/webrtc/04-protocol.md#close-codes).

## Security

- **Cookies:** CORS does not apply to WebSockets. If admission uses cookies, set
  `WebRtcRouter(..., allowed_origins=["https://app.example.com"])`.
- **Tokens in URLs:** browsers cannot set WebSocket headers. Prefer short-lived
  [session tickets](integrations/webrtc/02-admission-tickets.md) over access tokens.

Limits (`SessionLimits`, `max_sessions=`, a shared `SessionCapacity`) and TURN
credentials (`TurnRestCredentials`) bound resource use per session and process.

## Go deeper

- [Dishka scopes for sessions and messages](integrations/webrtc/01-canon-dishka.md)
- [Session tickets](integrations/webrtc/02-admission-tickets.md)
- [Video health observation](integrations/webrtc/03-media-health.md)
- [Signaling protocol](integrations/webrtc/04-protocol.md)
- Runnable: [`examples/webrtc/practice`](../examples/webrtc/practice),
  [remote video](../examples/webrtc/remote_video/README.md)

---

Previous: [10. Observability](10-observability.md) · [Learning path](11-webrtc.md) · Next: [12. MCP](12-mcp.md)
