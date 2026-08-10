from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.authentication.application import AuthenticationFailed
from app.presentation.schema import ErrorResponse


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthenticationFailed)
    async def authentication_failed(
        _: Request,
        __: AuthenticationFailed,
    ) -> JSONResponse:
        response = ErrorResponse(detail="Authentication required")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=response.model_dump(mode="json"),
            headers={"WWW-Authenticate": "Bearer"},
        )
