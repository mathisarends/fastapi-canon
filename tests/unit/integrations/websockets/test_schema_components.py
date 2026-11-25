import pytest
from fastapi_canon.integrations.websockets import ProtocolDefinitionError, RpcChannel
from fastapi_canon.integrations.websockets.schema import render_openrpc
from fastapi_canon.integrations.websockets.schema.components import type_name
from pydantic import AliasPath, BaseModel, Field, create_model


def test_type_name_requires_an_annotation_with_a_stable_name() -> None:
    with pytest.raises(ProtocolDefinitionError, match="no stable schema name"):
        type_name(42)


def test_rendering_rejects_two_distinct_types_sharing_a_schema_name() -> None:
    ParamsA = create_model("Dup", value=(int, ...))
    ParamsB = create_model("Dup", value=(str, ...))

    router = RpcChannel(namespace="greeting")

    @router.method("a")
    async def a(params: ParamsA) -> None: ...

    @router.method("b")
    async def b(params: ParamsB) -> None: ...

    with pytest.raises(ProtocolDefinitionError, match="Duplicate protocol schema name"):
        render_openrpc(router.protocol, title="Greeting")


@pytest.mark.parametrize("alias", ["first", AliasPath("nested", "value")])
def test_unrepresentable_request_alias_has_a_contract_error(
    alias: str | AliasPath,
) -> None:
    class Params(BaseModel):
        first: int
        second: str = Field(validation_alias=alias)

    channel = RpcChannel()

    @channel.method()
    async def read(params: Params) -> int:
        return params.first

    with pytest.raises(
        ProtocolDefinitionError, match=r"maps both|flat validation alias"
    ):
        render_openrpc(channel.protocol, title="Params")
