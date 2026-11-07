import json
from collections.abc import AsyncIterator, Iterator

import pytest
from examples.canonical_errors.app import ProjectMissing, project_missing
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_canon import (
    APIRouter,
    Composition,
    Error,
    ErrorOptions,
    Feature,
    RouterPolicy,
)
from jsonschema import Draft202012Validator


@pytest.mark.parametrize("inheritance", ["router", "policy", "composition"])
@pytest.mark.parametrize("synchronous", [False, True])
def test_inherited_stream_errors_match_terminal_contract(
    inheritance: str, synchronous: bool
) -> None:
    local_error = Error(ValueError, status=400, code="invalid", title="Invalid")
    child = APIRouter(prefix="/projects")

    async def asynchronous_events() -> AsyncIterator[int]:
        yield 1
        raise ProjectMissing("private failure")

    def synchronous_events() -> Iterator[int]:
        yield 1
        raise ProjectMissing("private failure")

    child.sse("/events", raises=[local_error], operation_id="projects.events")(
        synchronous_events if synchronous else asynchronous_events
    )
    policy = RouterPolicy(raises=[project_missing])
    parent = (
        APIRouter(raises=[project_missing])
        if inheritance == "router"
        else APIRouter(policy=policy if inheritance == "policy" else None)
    )
    parent.include_router(child, prefix="/nested")
    app = Composition(
        features=[Feature(name="projects", routers=[parent])],
        errors=ErrorOptions(type_base="urn:inherited:problem"),
        router_factory=(
            (lambda: APIRouter(prefix="/api", policy=policy))
            if inheritance == "composition"
            else (lambda: APIRouter(prefix="/api"))
        ),
    ).build(FastAPI())

    with TestClient(app) as client:
        response = client.get("/api/nested/projects/events")
    frame = response.text.split("event: canon.problem\n", 1)[1]
    problem = json.loads(frame.split("data: ", 1)[1])
    assert response.status_code == 200
    assert problem["code"] == "project_missing"
    assert problem["type"] == "urn:inherited:problem/project_missing"
    assert "status" not in problem
    assert "private failure" not in response.text
    assert "canon.complete" not in response.text
    document = app.openapi()
    operation = document["paths"]["/api/nested/projects/events"]["get"]
    assert "404" in operation["responses"]
    assert operation["x-canon-stream"]["problems"] == [
        {"$ref": "#/components/schemas/InvalidProblemEmbedded"},
        {"$ref": "#/components/schemas/ProjectMissingProblemEmbedded"},
    ]
    schema = {
        "anyOf": operation["x-canon-stream"]["problems"],
        "components": document["components"],
    }
    Draft202012Validator(schema).validate(problem)


def test_inherited_stream_errors_stay_local_to_each_inclusion() -> None:
    child = APIRouter()

    @child.sse("/events")
    async def events() -> AsyncIterator[int]:
        yield 1
        raise ProjectMissing("private failure")

    protected = APIRouter(raises=[project_missing])
    protected.include_router(child)
    first = Composition(features=[Feature(name="projects", routers=[protected])]).build(
        FastAPI()
    )
    second = Composition(
        features=[Feature(name="projects", routers=[child], errors=[project_missing])]
    ).build(FastAPI())

    with TestClient(first) as client:
        inherited = client.get("/events")
    with TestClient(second) as client:
        undeclared = client.get("/events")
    assert '"code": "project_missing"' in inherited.text
    assert '"code": "internal_server_error"' in undeclared.text
    assert (
        second.openapi()["paths"]["/events"]["get"]["x-canon-stream"]["problems"] == []
    )
