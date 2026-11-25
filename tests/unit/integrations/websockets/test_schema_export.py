import json

import pytest
from fastapi import FastAPI
from fastapi_canon import APIRouter
from fastapi_canon.integrations.websockets import RpcChannel, ServerVariable
from fastapi_canon.integrations.websockets.schema.export import (
    ProtocolReferenceError,
    load_contract_source,
    load_protocol,
    render_contract,
)

from tests.unit.integrations.websockets.test_contract import contract

REFERENCE = "tests.unit.integrations.websockets.test_contract:contract"
APP_REFERENCE = "tests.unit.integrations.websockets.test_schema_export:app"

health = RpcChannel(namespace="health")


@health.method()
async def ping() -> str:
    return "pong"


router = APIRouter(prefix="/api")
router.include_channel(health, path="/rpc")
app = FastAPI(title="Health")
app.include_router(router)


def test_contract_reference_resolves() -> None:
    assert load_contract_source(REFERENCE) is contract
    assert load_protocol(REFERENCE) is contract.protocol


def test_application_reference_exports_its_mounted_endpoints() -> None:
    exported = load_contract_source(
        APP_REFERENCE,
        base_url="wss://{host}",
        variables={"host": ServerVariable(default="localhost")},
    )
    document = json.loads(render_contract(exported))
    assert [method["name"] for method in document["methods"]] == ["health.ping"]
    assert document["servers"][0]["url"] == "wss://{host}/api/rpc"
    assert document["servers"][0]["variables"]["host"]["default"] == "localhost"


def test_server_options_require_an_application_reference() -> None:
    with pytest.raises(ProtocolReferenceError, match="FastAPI application"):
        load_contract_source(REFERENCE, base_url="wss://example.com")


def test_channel_and_service_references_are_rejected() -> None:
    with pytest.raises(ProtocolReferenceError, match="RpcChannel"):
        load_contract_source("tests.unit.integrations.websockets.test_contract:channel")
    with pytest.raises(ProtocolReferenceError, match="RpcDefinition"):
        load_contract_source("tests.unit.integrations.websockets.test_contract:service")


def test_contract_is_rendered_as_json() -> None:
    document = json.loads(render_contract(contract))
    assert document["info"]["title"] == "Control API"
    assert document["servers"][0]["name"] == "rpc"
