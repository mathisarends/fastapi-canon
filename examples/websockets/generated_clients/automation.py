import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature, Inject
from fastapi_canon.integrations.websockets import (
    RpcBinaryInput,
    RpcBinaryOutput,
    RpcChannel,
    RpcErrorBinding,
    RpcMessageType,
    RpcRejection,
    RpcSocket,
    RpcSocketProtocol,
)
from pydantic import BaseModel


class Task(BaseModel):
    id: str
    title: str
    done: bool = False


class TaskList(BaseModel):
    tasks: list[Task]


class TaskUpdated(BaseModel):
    task: Task


class Tab(BaseModel):
    id: str
    url: str


class BrowserNotFoundError(Exception):
    pass


BROWSER_NOT_FOUND = RpcErrorBinding(
    BrowserNotFoundError,
    code="browser_not_found",
    rejection=RpcRejection.NOT_FOUND,
    message="Unknown browser",
)


tasks = RpcChannel(namespace="tasks")
# Declared for the contract; clients without a binding get a generic socket.
CDP = RpcSocketProtocol(name="cdp", version="1.3")

browser = RpcChannel(namespace="browser")
tabs = browser.child("tabs")
screencast = browser.child("screencast")

_tasks: dict[str, Task] = {}
_updates: asyncio.Queue[Task] = asyncio.Queue()


@tasks.method("list", summary="List all tasks.")
async def list_tasks() -> TaskList:
    return TaskList(tasks=list(_tasks.values()))


@tasks.method(summary="Create a task.")
async def create(title: str) -> Task:
    task = Task(id=str(len(_tasks) + 1), title=title)
    _tasks[task.id] = task
    await _updates.put(task)
    return task


@tasks.method(summary="Delete a task.")
async def delete(task_id: str) -> bool:
    return _tasks.pop(task_id, None) is not None


@tasks.event(summary="Stream task updates.")
async def updated() -> AsyncIterator[TaskUpdated]:
    while True:
        yield TaskUpdated(task=await _updates.get())


@tabs.method(summary="Open a browser tab.")
async def open(url: str) -> Tab:
    return Tab(id="tab-1", url=url)


@screencast.method(summary="Start the browser screencast.")
async def start(quality: int = 80) -> None:
    return None


router = APIRouter()
router.include_channel(tasks, path="/automation/rpc", name="production")
router.include_channel(tabs, path="/browser/rpc", name="browser")
router.include_channel(
    screencast,
    path="/browser/stream",
    name="streaming",
    subprotocol="fastapi_canon.integrations.websockets.jsonrpc",
)

frames_router = APIRouter(prefix="/browser/screencast", namespace="browser.screencast")


@frames_router.binary_stream(
    "",
    content_type="image/jpeg",
    summary="Raw screencast frames as binary WebSocket messages.",
    name="frames",
)
async def frames() -> AsyncIterator[bytes]:
    yield b"\xff\xd8\xff\xd9"


@frames_router.binary_stream(
    "/upload",
    input_content_type="video/webm",
    summary="Upload a recorded screencast as binary WebSocket messages.",
    name="upload",
)
async def upload(recording: Inject[RpcBinaryInput]) -> None:
    async for _chunk in recording:
        pass


@frames_router.binary_stream(
    "/control",
    content_type="image/jpeg",
    input_content_type="application/x-input-event",
    summary="Receive frames while sending input events back.",
    name="control",
)
async def control(
    events: Inject[RpcBinaryInput],
    output: Inject[RpcBinaryOutput],
) -> None:
    async for _event in events:
        await output.send(b"\xff\xd8\xff\xd9")


cdp_router = APIRouter(prefix="/browser", namespace="browser")


@cdp_router.socket(
    "/cdp",
    protocol=CDP,
    messages=(RpcMessageType.TEXT,),
    closes=(BROWSER_NOT_FOUND,),
    summary="Proxy the Chrome DevTools Protocol unchanged.",
    name="cdp",
)
async def cdp(socket: Inject[RpcSocket]) -> None:
    async for message in socket:
        await socket.send(message)


app = Composition(
    features=[
        Feature(name="automation", routers=[router, frames_router, cdp_router]),
    ]
).build(FastAPI(title="Automation", description="Typed JSON-RPC API."))
