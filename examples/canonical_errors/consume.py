"""Run the committed SDK against the canonical-errors server over real transports."""

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any


async def consume(base_url: str) -> dict[str, Any]:
    # The generator emits a package directory; expose it like an installed SDK.
    sdk_path = str(Path(__file__).parent / "generated/python")
    sys.path.insert(0, sdk_path)
    try:
        sdk = importlib.import_module("api_client")
        models = importlib.import_module("api_client.models")
        observed: list[int] = []
        problems: list[str] = []
        async with sdk.ApiClient(base_url) as client:
            project = await client.projects.get(project_id=1)
            events = [
                event.sequence async for event in client.projects.events(project_id=1)
            ]
            await client.projects.cancel(project_id=1)

            async def stream(project_id: int) -> None:
                observed.clear()
                async for event in client.projects.events(project_id=project_id):
                    observed.append(event.sequence)

            for name, operation in (
                ("http", lambda: client.projects.get(project_id=0)),
                ("admission", lambda: stream(0)),
                ("terminal", lambda: stream(2)),
                ("rpc", lambda: client.projects.cancel(project_id=0)),
            ):
                try:
                    await operation()
                except sdk.ProblemError as error:
                    assert isinstance(error.problem, models.ProjectMissingProblem)
                    assert (
                        error.problem.type
                        == "https://api.example.com/problems/project_missing"
                    )
                    assert error.problem.code == "project_missing"
                    assert error.problem.replacement is None
                    if name in {"admission", "terminal"}:
                        assert observed == ([] if name == "admission" else [1])
                    problems.append(name)
                else:
                    raise AssertionError(f"{name} did not raise the shared problem")
            return {
                "project": project.id,
                "events": events,
                "rpc": "cancelled",
                "problems": problems,
            }
    finally:
        sys.path.remove(sdk_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()

    async def run() -> None:
        async with asyncio.timeout(20):
            print(json.dumps(await consume(arguments.base_url)))

    asyncio.run(run())


if __name__ == "__main__":
    main()
