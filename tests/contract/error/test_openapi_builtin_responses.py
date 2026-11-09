from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate
from pydantic import BaseModel

from fastapi_canon import APIRouter, Composition, Error, ErrorOptions, Feature


class FirstFailure(Exception):
    pass


class SecondFailure(Exception):
    pass


class RequestBody(BaseModel):
    name: str


@pytest.mark.parametrize("status", [422, 500])
@pytest.mark.parametrize(
    ("error_count", "expected_discriminator"), [(1, "code"), (2, "type")]
)
def test_builtin_problem_mapping_matches_runtime_discriminator(
    status: int, error_count: int, expected_discriminator: str
) -> None:
    errors: list[Error[Any]] = [
        Error(
            FirstFailure,
            status=status,
            code="first_failure",
            title="First failure",
            type="urn:example:first-failure",
        ),
        Error(
            SecondFailure,
            status=status,
            code="second_failure",
            title="Second failure",
        ),
    ]
    router = APIRouter()

    @router.post("/items", raises=errors[:error_count])
    async def create_item(body: RequestBody) -> None:
        if body.name == "first":
            raise FirstFailure
        raise RuntimeError("Private failure")

    app = Composition(
        features=[Feature(name="items", routers=[router])],
        errors=ErrorOptions(type_base="https://example.com/problems"),
    ).build(FastAPI())
    document = app.openapi()
    validate(document)
    schema = document["paths"]["/items"]["post"]["responses"][str(status)]["content"][
        "application/problem+json"
    ]["schema"]
    discriminator = schema["discriminator"]
    property_name = discriminator["propertyName"]
    assert property_name == expected_discriminator
    components = document["components"]["schemas"]
    assert discriminator["mapping"] == {
        components[variant["$ref"].rsplit("/", 1)[-1]]["properties"][property_name][
            "const"
        ]: variant["$ref"]
        for variant in schema["oneOf"]
    }
    builtin_component = (
        "RequestValidationProblem" if status == 422 else "InternalServerProblem"
    )
    expected_code = (
        "request_validation_error" if status == 422 else "internal_server_error"
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/items", json={} if status == 422 else {"name": "bad"})
        domain_response = client.post("/items", json={"name": "first"})

    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    payload = response.json()
    assert payload["code"] == expected_code
    assert payload["type"] == f"https://example.com/problems/{expected_code}"
    assert discriminator["mapping"][payload[property_name]] == (
        f"#/components/schemas/{builtin_component}"
    )
    validator = Draft202012Validator({**schema, "components": document["components"]})
    validator.validate(payload)
    assert domain_response.status_code == status
    domain_payload = domain_response.json()
    assert domain_payload["type"] == "urn:example:first-failure"
    assert discriminator["mapping"][domain_payload[property_name]] == (
        "#/components/schemas/FirstFailureProblem"
    )
    validator.validate(domain_payload)
