import json

from fastapi import FastAPI
from fastapi_canon import APIRouter, Composition, Feature, export_contracts
from fastapi_canon.integrations.websockets import RpcChannel
from pydantic import BaseModel


class SearchResult(BaseModel):
    items: list[str]
    next_page_token: str | None = None


search = RpcChannel(namespace="search")


@search.method()
async def run(project_id: str, query: str, max_results: int = 10) -> SearchResult:
    return SearchResult(items=[])


router = APIRouter()
router.include_channel(search, path="/rpc", version=3)
app = Composition(features=[Feature(name="search", routers=[router])]).build(
    FastAPI(title="Search API")
)


def main() -> None:
    openrpc = export_contracts(
        app, kinds=("openrpc",), base_url="ws://localhost:8000"
    ).openrpc
    assert openrpc is not None
    print("OpenRPC method:")
    print(json.dumps(openrpc["methods"][0], indent=2))


if __name__ == "__main__":
    main()
