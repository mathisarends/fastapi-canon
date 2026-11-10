import argparse
import asyncio
from contextlib import aclosing

from httpx2 import AsyncClient

from .dist.streams.python import Client, ProblemError


async def read_events(
    base_url: str,
    http_client: AsyncClient,
    project_id: int,
    *,
    limit: int | None = None,
) -> list[int]:
    """Borrow the caller's pool; close the stream on completion or early exit."""
    values: list[int] = []
    async with (
        Client(base_url, http_client=http_client) as client,
        aclosing(client.projects.projects_events(project_id=project_id)) as events,
    ):
        async for event in events:
            values.append(event.sequence)
            if limit is not None and len(values) >= limit:
                break
    return values


async def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Consume the separate Python SSE client"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", type=int, default=1)
    args = parser.parse_args()
    async with AsyncClient() as http_client:
        try:
            print(await read_events(args.base_url, http_client, args.project_id))
        except ProblemError as error:
            print(f"{error.problem.type}: {error.problem.code}")


if __name__ == "__main__":
    asyncio.run(_main())
