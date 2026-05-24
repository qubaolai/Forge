"""应用层配置: App / Auth / CORS."""

from pydantic import BaseModel, field_validator


class AuthConfig(BaseModel):
    model_config = {"extra": "forbid"}

    jwt_secret: str
    jwt_algorithm: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int
    cookie_secure: bool = False
    cookie_samesite: str = "lax"


class AppConfig(BaseModel):
    model_config = {"extra": "forbid"}

    log_level: str = "INFO"
    port: int = 8080
    auth: AuthConfig

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR"}
        if v.upper() not in allowed:
            raise ValueError(f"log_level 必须是 {allowed} 之一, got: {v!r}")
        return v.upper()


class CORSConfig(BaseModel):
    model_config = {"extra": "forbid"}

    allow_origins: str = "http://localhost:3000"
    allow_credentials: bool = True

    def origins_list(self) -> list[str]:
        if self.allow_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.allow_origins.split(",") if o.strip()]


class MiddlewareConfig(BaseModel):
    model_config = {"extra": "forbid"}

    cors: CORSConfig = CORSConfig()
