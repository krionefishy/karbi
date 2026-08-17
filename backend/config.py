from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_environment: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://karbi:karbi@localhost:5432/karbi"
    redis_url: str = "redis://localhost:6379/0"

    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "localhost:9092"

    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: SecretStr = SecretStr("")
    s3_bucket: str = ""
    s3_region: str = "ru-central-1"
    s3_verify_tls: bool = True

    jwt_secret: SecretStr = SecretStr("change-me")
    access_token_ttl_hours: int = 24
    refresh_token_ttl_days: int = 7
    credentials_encryption_key: SecretStr = SecretStr("")

    rate_limit_window_seconds: int = 60
    rate_limit_requests: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
