from fastapi import status
from fastapi_canon import APIRouter

from app.presentation.schema import Schema

router = APIRouter(tags=["system"])


class HealthResponse(Schema):
    status: str


@router.get(
    "/health",
    operation_id="get_health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
