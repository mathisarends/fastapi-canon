from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.features.tasks.application import TaskNotFound
from app.presentation.schema import ErrorResponse


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(TaskNotFound)
    async def task_not_found(_: Request, exception: TaskNotFound) -> JSONResponse:
        response = ErrorResponse(detail=f"Task {exception.args[0]} was not found")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=response.model_dump(mode="json"),
        )
