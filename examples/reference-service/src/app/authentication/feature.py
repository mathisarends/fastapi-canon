from fastapi_canon import Feature

from app.authentication.infrastructure.provider import AuthenticationProvider

feature = Feature(
    name="authentication",
    providers=(AuthenticationProvider,),
)
