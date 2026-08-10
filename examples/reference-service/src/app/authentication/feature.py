from app.authentication.infrastructure.provider import AuthenticationProvider
from app.authentication.presentation.errors import register_exception_handlers
from app.presentation.feature import Feature

feature = Feature(
    name="authentication",
    providers=(AuthenticationProvider,),
    register_exception_handlers=register_exception_handlers,
)
