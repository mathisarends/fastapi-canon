from fastapi_canon import Error

from app.features.tasks.application import TaskNotFound

task_not_found = Error(
    TaskNotFound,
    status=404,
    code="task_not_found",
    title="Task not found",
    detail="The requested task does not exist.",
)
