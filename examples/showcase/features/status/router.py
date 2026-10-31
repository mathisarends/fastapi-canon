from fastapi_canon import CanonRouter

router = CanonRouter(tags=["status"])


@router.get("/health", operation_id="get_health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
