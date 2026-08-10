from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.features.tasks.domain.entities import Task
from app.features.tasks.domain.repository import TaskRepository
from app.features.tasks.infrastructure.models import TaskModel
from app.infrastructure.database import SqlRepository


class SqlTaskRepository(SqlRepository[TaskModel, Task], TaskRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, TaskModel)

    def to_domain(self, model: TaskModel) -> Task:
        return Task(
            id=model.id,
            created_time=model.created_at,
            title=model.title,
            completed=model.completed,
        )

    def to_model(self, entity: Task) -> TaskModel:
        return TaskModel(
            id=entity.id,
            created_at=entity.created_at,
            title=entity.title,
            completed=entity.completed,
        )

    async def get_by_id(self, *, task_id: UUID) -> Task | None:
        return await self.find_by_id(task_id)

    async def list_recent(self, *, limit: int) -> list[Task]:
        statement = (
            select(TaskModel).order_by(col(TaskModel.created_at).desc()).limit(limit)
        )
        models = (await self._session.scalars(statement)).all()
        return [self.to_domain(model) for model in models]

    async def save(self, *, task: Task) -> Task:
        return await self.save_entity(task)
