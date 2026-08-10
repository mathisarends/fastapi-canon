from uuid import UUID

import pytest

from app.features.tasks.application import TaskNotFound, TaskService
from app.features.tasks.domain import Task, TaskRepository


class InMemoryTaskRepository(TaskRepository):
    def __init__(self) -> None:
        self.tasks: dict[UUID, Task] = {}

    async def get_by_id(self, *, task_id: UUID) -> Task | None:
        return self.tasks.get(task_id)

    async def list_recent(self, *, limit: int) -> list[Task]:
        return list(self.tasks.values())[-limit:]

    async def save(self, *, task: Task) -> Task:
        self.tasks[task.id] = task
        return task


async def test_complete_updates_a_task() -> None:
    repository = InMemoryTaskRepository()
    task = await repository.save(task=Task(title="Ship the canon"))
    service = TaskService(repository)

    completed = await service.complete(task_id=task.id)

    assert completed.completed is True
    assert repository.tasks[task.id] == completed


async def test_complete_rejects_unknown_task() -> None:
    service = TaskService(InMemoryTaskRepository())

    with pytest.raises(TaskNotFound):
        await service.complete(task_id=UUID(int=0))
