from collections.abc import AsyncIterator
from typing import Literal

from dishka import Provider, Scope
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature, Inject, export_contracts
from fastapi_canon.integrations.websockets import RpcChannel
from pydantic import BaseModel


class JobStarted(BaseModel):
    type: Literal["job.started"] = "job.started"
    job_id: str


class JobFinished(BaseModel):
    type: Literal["job.finished"] = "job.finished"
    job_id: str
    succeeded: bool


type JobUpdate = JobStarted | JobFinished

jobs = RpcChannel(namespace="jobs")


class JobEvents:
    async def subscribe(self) -> AsyncIterator[JobUpdate]:
        yield JobStarted(job_id="job-42")


@jobs.event(
    "changed",
    summary="Publish job lifecycle changes.",
)
async def job_changes(events: Inject[JobEvents]) -> AsyncIterator[JobUpdate]:
    async for event in events.subscribe():
        yield event


router = APIRouter()
router.include_channel(jobs, path="/rpc")
providers = Provider()
providers.provide(JobEvents, scope=Scope.APP)
app = Composition(
    features=[Feature(name="jobs", routers=[router], providers=[providers])]
).build(FastAPI(title="Jobs"))


def main() -> None:
    contract = export_contracts(app, kinds=("openrpc",)).openrpc
    assert contract is not None
    for notification in contract["x-rpc-notifications"]:
        print(f"Notification: {notification['name']}")


if __name__ == "__main__":
    main()
