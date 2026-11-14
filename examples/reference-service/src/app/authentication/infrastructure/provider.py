from dishka import Provider, Scope, provide

from app.authentication.application import TokenVerifier
from app.authentication.infrastructure.settings import AuthenticationSettings
from app.authentication.infrastructure.verifier import LocalTokenVerifier


class AuthenticationProvider(Provider):
    # Pydantic's environment controls are configuration, not DI dependencies.
    settings = provide(
        staticmethod(lambda: AuthenticationSettings()),
        provides=AuthenticationSettings,
        scope=Scope.APP,
    )
    token_verifier = provide(
        LocalTokenVerifier, provides=TokenVerifier, scope=Scope.APP
    )
