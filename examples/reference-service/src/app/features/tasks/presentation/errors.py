from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.features.tasks.application import TaskNotFound


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(TaskNotFound)
    async def task_not_found(_: Request, exception: TaskNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"Task {exception.args[0]} was not found"},
        )
