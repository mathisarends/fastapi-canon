from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query, status

from app.features.tasks.application import TaskService
from app.features.tasks.presentation.mapper import to_list_response, to_response
from app.features.tasks.presentation.schemas import (
    CreateTaskRequest,
    ErrorResponse,
    TaskListResponse,
    TaskResponse,
)

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    route_class=DishkaRoute,
)


@router.post(
    "",
    operation_id="create_task",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    body: CreateTaskRequest,
    service: FromDishka[TaskService],
) -> TaskResponse:
    task = await service.create(title=body.title)
    return to_response(task)


@router.get(
    "",
    operation_id="list_tasks",
    response_model=TaskListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_tasks(
    service: FromDishka[TaskService],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> TaskListResponse:
    tasks = await service.list_recent(limit=limit)
    return to_list_response(tasks)


@router.post(
    "/{task_id}/completion",
    operation_id="complete_task",
    response_model=TaskResponse,
    status_code=status.HTTP_200_OK,
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
async def complete_task(
    task_id: UUID,
    service: FromDishka[TaskService],
) -> TaskResponse:
    task = await service.complete(task_id=task_id)
    return to_response(task)
