from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature, export_contracts
from fastapi_canon.integrations.websockets import RpcChannel
from pydantic import BaseModel


class StatusResult(BaseModel):
    status: str


class ProfileResult(BaseModel):
    name: str


system = RpcChannel(namespace="system")
account = RpcChannel(namespace="account")


@system.method()
async def status() -> StatusResult:
    return StatusResult(status="ready")


@account.method(summary="Return the current profile.")
async def profile() -> ProfileResult:
    return ProfileResult(name="Mathis")


session = RpcChannel()
session.include_channel(system)
session.include_channel(account)

router = APIRouter()
router.include_channel(session, path="/rpc", version=2)
app = Composition(features=[Feature(name="service", routers=[router])]).build(
    FastAPI(title="Service")
)


def main() -> None:
    contract = export_contracts(app, kinds=("openrpc",)).openrpc
    assert contract is not None
    for method in contract["methods"]:
        print(method["name"])


if __name__ == "__main__":
    main()
