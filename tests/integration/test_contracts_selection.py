from copy import deepcopy

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from fastapi_canon import Composition, Feature, export_contracts
from fastapi_canon.contracts import ContractKind, ContractSelection
from fastapi_canon.integrations.webrtc import Session
from fastapi_canon.integrations.webrtc.router import WebRtcRouter
from fastapi_canon.integrations.websockets import (
    CanonWebSocketRouter,
    ProtocolDefinitionError,
    RpcChannel,
)


@pytest.mark.parametrize(
    ("second_name", "second_version", "conflict"),
    [
        ("control", 1, "Ambiguous RPC endpoint"),
        ("other", 2, "different protocol versions"),
    ],
)
def test_feature_selection_precedes_rpc_contract_validation(
    second_name: str, second_version: int, conflict: str
) -> None:
    first = _rpc_feature("browser", "/v1/rpc", endpoint="control")
    second = _rpc_feature(
        "session", "/v2/rpc", endpoint=second_name, version=second_version
    )
    app = Composition(
        features=(first, second),
        router_factory=lambda: APIRouter(prefix="/api"),
    ).build(FastAPI())

    with pytest.raises(ProtocolDefinitionError, match=conflict):
        export_contracts(app, kinds=(ContractKind.OPENRPC,))

    first_contract = export_contracts(
        app,
        kinds=(ContractKind.OPENRPC,),
        selection=ContractSelection(features=(first,)),
    ).openrpc
    second_contract = export_contracts(
        app,
        kinds=(ContractKind.OPENRPC,),
        selection=ContractSelection(features=(second,)),
    ).openrpc

    assert first_contract is not None
    assert second_contract is not None
    assert first_contract["servers"][0]["url"] == "{baseUrl}/api/v1/rpc"
    assert second_contract["servers"][0]["url"] == "{baseUrl}/api/v2/rpc"
    assert [method["name"] for method in first_contract["methods"]] == ["control.ping"]
    assert [method["name"] for method in second_contract["methods"]] == ["control.ping"]


def test_feature_selection_preserves_http_methods_schema_closure_and_app_cache() -> (
    None
):
    class Child(BaseModel):
        value: int

    class Selected(BaseModel):
        child: Child

    class Other(BaseModel):
        secret: str

    public = APIRouter(prefix="/items")
    private = APIRouter(prefix="/items")

    @public.get("")
    async def read() -> Selected:
        return Selected(child=Child(value=1))

    @private.post("")
    async def write() -> Other:
        return Other(secret="private")

    feature = Feature(name="public", routers=(public,))
    app = Composition(
        features=(feature, Feature(name="private", routers=(private,))),
    ).build(FastAPI())
    before = deepcopy(app.openapi())

    # Later changes to the declaration are not proof of installed ownership.
    @public.get("/late")
    async def late() -> str:
        return "late"

    exported = export_contracts(
        app,
        kinds=(ContractKind.OPENAPI,),
        selection=ContractSelection(features=(feature,)),
    ).openapi

    assert exported is not None
    assert set(exported["paths"]) == {"/items"}
    assert set(exported["paths"]["/items"]) == {"get"}
    assert set(exported["components"]["schemas"]) == {
        "Child",
        "Selected",
        "InternalServerProblem",
    }
    assert app.openapi() == before
    with TestClient(app) as client:
        assert client.get("/items").json() == {"child": {"value": 1}}
        assert client.post("/items").json() == {"secret": "private"}


def test_feature_references_select_all_mounts_and_paths_narrow_the_occurrence() -> None:
    feature = _rpc_feature("browser", "/rpc", endpoint="control")
    child = Composition(features=(feature,)).build(FastAPI())
    parent = FastAPI()
    parent.mount("/v1", child)
    parent.mount("/v2", child)

    with pytest.raises(ProtocolDefinitionError, match="'/v1/rpc' and '/v2/rpc'"):
        export_contracts(
            parent,
            kinds=(ContractKind.OPENRPC,),
            selection=ContractSelection(features=(feature,)),
        )

    document = export_contracts(
        parent,
        kinds=(ContractKind.OPENRPC,),
        selection=ContractSelection(features=(feature,), paths=("/v2/rpc",)),
    ).openrpc

    assert document is not None
    assert document["servers"][0]["url"] == "{baseUrl}/v2/rpc"


def test_path_selection_supports_applications_without_composition() -> None:
    feature = _rpc_feature("browser", "/rpc", endpoint="control")
    app = FastAPI()
    app.include_router(feature.routers[0], prefix="/api")

    document = export_contracts(
        app,
        kinds=(ContractKind.OPENRPC,),
        selection=ContractSelection(paths=("/api/rpc",)),
    ).openrpc

    assert document is not None
    assert document["servers"][0]["url"] == "{baseUrl}/api/rpc"


@pytest.mark.parametrize("name", ["browser", "foreign"])
def test_uninstalled_feature_objects_are_rejected_even_with_the_same_name(
    name: str,
) -> None:
    installed = _rpc_feature("browser", "/rpc", endpoint="control")
    app = Composition(features=(installed,)).build(FastAPI())
    foreign = Feature(name=name, routers=installed.routers)

    with pytest.raises(ValueError, match="exact Feature instances"):
        export_contracts(
            app,
            kinds=(ContractKind.OPENRPC,),
            selection=ContractSelection(features=(foreign,)),
        )


@pytest.mark.parametrize("path", ["/missing", "/rpc"])
def test_selection_rejects_unknown_paths_and_empty_intersections(path: str) -> None:
    http = Feature(name="http")
    rpc = _rpc_feature("browser", "/rpc", endpoint="control")
    app = Composition(features=(http, rpc)).build(FastAPI())

    with pytest.raises(ValueError, match=r"[Nn]o .*routes"):
        export_contracts(
            app,
            kinds=(ContractKind.OPENRPC,),
            selection=ContractSelection(features=(http,), paths=(path,)),
        )


def test_openapi_selection_retains_mounted_application_boundaries() -> None:
    child = FastAPI()

    @child.get("/health")
    async def health() -> str:
        return "ok"

    parent = FastAPI()
    parent.mount("/backend", child)

    with pytest.raises(ValueError, match="separate OpenAPI documents"):
        export_contracts(
            parent,
            kinds=(ContractKind.OPENAPI,),
            selection=ContractSelection(paths=("/backend/health",)),
        )


def test_webrtc_selection_precedes_duplicate_session_name_validation() -> None:
    first_router = WebRtcRouter()
    second_router = WebRtcRouter()

    @first_router.session("/first", name="media")
    def first_session() -> Session:
        raise AssertionError("Contract export must not acquire a media session")

    @second_router.session("/second", name="media")
    def second_session() -> Session:
        raise AssertionError("Contract export must not acquire a media session")

    first = Feature(name="first", routers=(first_router,))
    second = Feature(name="second", routers=(second_router,))
    app = Composition(features=(first, second)).build(FastAPI())

    with pytest.raises(ValueError, match="declared more than once"):
        export_contracts(app, kinds=(ContractKind.WEBRTC,))

    document = export_contracts(
        app,
        kinds=(ContractKind.WEBRTC,),
        selection=ContractSelection(features=(first,)),
    ).webrtc

    assert document is not None
    assert [(session["name"], session["path"]) for session in document["sessions"]] == [
        ("media", "/first")
    ]


def _rpc_feature(name: str, path: str, *, endpoint: str, version: int = 1) -> Feature:
    channel = RpcChannel("control")

    @channel.method()
    async def ping() -> str:
        return "pong"

    router = CanonWebSocketRouter(version=version)
    router.socket(path, channels=(channel,), name=endpoint)
    return Feature(name=name, routers=(router,))
