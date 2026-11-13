import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature
from fastapi_canon.integrations.sse.codegen.ir import build_ir
from fastapi_canon.integrations.sse.codegen.openapi import ContractError, export_schema
from fastapi_canon.integrations.sse.codegen.python import generate_python
from fastapi_canon.integrations.sse.codegen.typescript import generate_typescript
from pydantic import BaseModel
from tests.unit.integrations.sse.codegen.helpers import (
    compile_typescript,
    load_package,
)


class Event(BaseModel):
    source: str


def _app() -> FastAPI:
    projects = APIRouter(namespace="projects")
    members = APIRouter(namespace="projects.members")
    agent = APIRouter(tags=["agent"])

    @projects.sse("/projects/{project_id}/events", tags=["projects"])
    async def events(project_id: int) -> AsyncIterator[Event]:
        yield Event(source=f"projects.events:{project_id}")

    @members.sse("/projects/{project_id}/members/updates")
    async def updates(project_id: int) -> AsyncIterator[Event]:
        yield Event(source=f"projects.members.updates:{project_id}")

    @agent.sse("/agent/follow")
    async def follow() -> AsyncIterator[Event]:
        yield Event(source="agent.follow")

    feature = Feature(name="streams", routers=(projects, members, agent))
    return Composition(features=(feature,)).build(FastAPI())


async def test_python_client_groups_follow_operation_namespaces(tmp_path):
    app = _app()
    files = generate_python(build_ir(export_schema(app)))
    with load_package(tmp_path, files) as sdk:
        async with (
            asyncio.timeout(15),
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as http,
            sdk.Client("http://test", http_client=http) as client,
        ):
            received = [
                event.source
                for stream in (
                    client.projects.events(project_id=1),
                    client.projects.members.updates(project_id=2),
                    client.agent.follow(),
                )
                async for event in stream
            ]
    assert received == [
        "projects.events:1",
        "projects.members.updates:2",
        "agent.follow",
    ]


def test_typescript_client_groups_follow_operation_namespaces(tmp_path):
    files = generate_typescript(build_ir(export_schema(_app())))
    result = compile_typescript(
        tmp_path,
        files,
        'import { createClient } from "./index.js";\n'
        'const client = createClient({ baseUrl: "http://test" });\n'
        "export const streams = [\n"
        "  client.projects.events({ projectId: 1 }),\n"
        "  client.projects.members.updates({ projectId: 2 }),\n"
        "  client.agent.follow(),\n"
        "];\n"
        "// @ts-expect-error: the namespace is a group, not a method prefix\n"
        "client.projects.projectsEvents({ projectId: 1 });\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("operation_ids", "message"),
    [
        (("projects.members", "projects.members.updates"), "share a name"),
        (("client.events",), "Reserved stream group"),
    ],
)
def test_client_paths_reject_ambiguous_generated_names(operation_ids, message):
    document = export_schema(_app())
    operations = [
        path["get"]
        for path in document["paths"].values()
        if "text/event-stream" in path["get"]["responses"]["200"]["content"]
    ]
    for operation, operation_id in zip(operations, operation_ids, strict=False):
        operation["operationId"] = operation_id
    with pytest.raises(ContractError, match=message):
        build_ir(document)
