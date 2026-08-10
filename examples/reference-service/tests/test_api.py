from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.features.tasks.infrastructure.models  # noqa: F401
from app.main import create_app


@pytest.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)

    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as test_client:
            yield test_client

    await engine.dispose()


async def test_task_http_flow(client: AsyncClient) -> None:
    health = await client.get("/health")
    assert health.json() == {"status": "ok"}

    created = await client.post("/api/v1/tasks", json={"title": "Ship the canon"})
    assert created.status_code == 201

    task_id = created.json()["id"]
    completed = await client.post(f"/api/v1/tasks/{task_id}/completion")
    assert completed.status_code == 200
    assert completed.json()["completed"] is True

    listed = await client.get("/api/v1/tasks")
    assert listed.status_code == 200
    assert [task["id"] for task in listed.json()["items"]] == [task_id]

    openapi = (await client.get("/openapi.json")).json()
    operation_ids = {
        operation["operationId"]
        for path in openapi["paths"].values()
        for operation in path.values()
    }
    assert operation_ids == {
        "complete_task",
        "create_task",
        "get_health",
        "list_tasks",
    }
