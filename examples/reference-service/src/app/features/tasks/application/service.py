from uuid import UUID

from app.features.tasks.domain import Task, TaskRepository


class TaskNotFound(Exception):
    pass


class TaskService:
    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    async def create(self, *, title: str) -> Task:
        task = Task(title=title)
        return await self._repository.save(task=task)

    async def list_recent(self, *, limit: int = 50) -> list[Task]:
        return await self._repository.list_recent(limit=limit)

    async def complete(self, *, task_id: UUID) -> Task:
        task = await self._repository.get_by_id(task_id=task_id)
        if task is None:
            raise TaskNotFound(task_id)
        return await self._repository.save(task=task.complete())
