from uuid import UUID

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthenticationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTHENTICATION_",
        env_file=".env",
        extra="ignore",
    )

    local_bearer_token: SecretStr = SecretStr("local-development-token")
    local_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")

    def bearer_token(self) -> str:
        return self.local_bearer_token.get_secret_value()
