from dishka import Provider, Scope, provide

from app.features.tasks.application import TaskService
from app.features.tasks.domain import TaskRepository
from app.features.tasks.infrastructure.repository import SqlTaskRepository


class TaskProvider(Provider):
    task_repository = provide(
        SqlTaskRepository, provides=TaskRepository, scope=Scope.REQUEST
    )
    task_service = provide(TaskService, scope=Scope.REQUEST)
