"""Call the showcase through its generated SDK, including a typed public error."""

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path


async def _consume(base_url: str) -> None:
    sdk_path = str(Path(__file__).parent / "dist/generated/python")
    sys.path.insert(0, sdk_path)
    try:
        sdk = importlib.import_module("catalog_client")
        async with sdk.ApiClient(base_url) as client:
            product = await client.catalog.get(product_id="coffee")
            print(json.dumps({"id": product.id, "available": product.available}))
            try:
                await client.catalog.get(product_id="missing")
            except sdk.ProblemError as error:
                assert error.problem.code == "product_not_found"
                print(
                    json.dumps(
                        {"code": error.problem.code, "status": error.problem.status}
                    )
                )
            else:
                raise AssertionError("Missing product did not raise ProblemError")
    finally:
        sys.path.remove(sdk_path)


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()

    async def run() -> None:
        async with asyncio.timeout(15):
            await _consume(arguments.base_url)

    asyncio.run(run())


if __name__ == "__main__":
    _main()
