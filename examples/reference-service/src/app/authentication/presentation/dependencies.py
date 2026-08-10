from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import HTTPConnection

from app.authentication.application import AuthenticationFailed, TokenVerifier

bearer = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Opaque bearer token for the local reference service.",
)


async def _token_verifier(connection: HTTPConnection) -> TokenVerifier:
    verifier = await connection.state.dishka_container.get(TokenVerifier)
    return cast(TokenVerifier, verifier)


def authenticated_user_id(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer),
    ],
    verifier: Annotated[TokenVerifier, Depends(_token_verifier)],
) -> UUID:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationFailed
    principal = verifier.verify(token=credentials.credentials)
    return principal.user_id


AuthenticatedUserId = Annotated[UUID, Depends(authenticated_user_id)]
