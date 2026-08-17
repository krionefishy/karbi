import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::-([^}]*))?}")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "1", "yes", "on"}:
        return True
    if isinstance(value, str) and value.lower() in {"false", "0", "no", "off", ""}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _expand_environment(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.groups()
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ValueError(f"Environment variable {name} is required by the config")

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class AppConfig:
    environment: str = "local"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    cors_origins: tuple[str, ...] = ()
    trust_proxy_headers: bool = False


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    user: str = "karbi"
    password: str = field(default="karbi", repr=False)
    database: str = "karbi"
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 5

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{quote_plus(self.user)}:{quote_plus(self.password)}@"
            f"{self.host}:{self.port}/{quote_plus(self.database)}"
        )


@dataclass(frozen=True, slots=True)
class RedisConfig:
    host: str = "localhost"
    port: int = 6379
    database: int = 0
    password: str = field(default="", repr=False)

    @property
    def url(self) -> str:
        credentials = f":{quote_plus(self.password)}@" if self.password else ""
        return f"redis://{credentials}{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True, slots=True)
class KafkaConfig:
    enabled: bool = False
    bootstrap_servers: str = "localhost:9092"
    consumer_group: str = "karbi"
    max_request_size: int = 25_165_824
    topic_partitions: int = 3
    topic_replication_factor: int = 1


@dataclass(frozen=True, slots=True)
class S3Config:
    enabled: bool = False
    endpoint_url: str = ""
    tenant_id: str = ""
    key_id: str = ""
    secret_key: str = field(default="", repr=False)
    bucket: str = ""
    region: str = "ru-central-1"
    verify_tls: bool = True


@dataclass(frozen=True, slots=True)
class AuthConfig:
    jwt_secret: str = field(default="change-me", repr=False)
    algorithm: str = "HS256"
    access_token_ttl_seconds: int = 86_400
    refresh_token_ttl_seconds: int = 604_800


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    credential_encryption_keys: tuple[str, ...] = field(default=(), repr=False)
    credential_fingerprint_key: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    enabled: bool = True
    requests: int = 30
    window_seconds: int = 60


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    poll_interval_seconds: int = 30


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppConfig
    database: DatabaseConfig
    redis: RedisConfig
    kafka: KafkaConfig
    s3: S3Config
    auth: AuthConfig
    security: SecurityConfig
    rate_limit: RateLimitConfig
    worker: WorkerConfig

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        app = dict(data.get("app", {}))
        app["trust_proxy_headers"] = _as_bool(app.get("trust_proxy_headers", False))
        database = dict(data.get("database", {}))
        database["port"] = int(database.get("port", 5432))
        database["echo"] = _as_bool(database.get("echo", False))
        database["pool_size"] = int(database.get("pool_size", 5))
        database["max_overflow"] = int(database.get("max_overflow", 5))
        redis = dict(data.get("redis", {}))
        redis["port"] = int(redis.get("port", 6379))
        redis["database"] = int(redis.get("database", 0))
        kafka = dict(data.get("kafka", {}))
        kafka["enabled"] = _as_bool(kafka.get("enabled", False))
        kafka["max_request_size"] = int(kafka.get("max_request_size", 25_165_824))
        kafka["topic_partitions"] = int(kafka.get("topic_partitions", 3))
        kafka["topic_replication_factor"] = int(kafka.get("topic_replication_factor", 1))
        s3 = dict(data.get("s3", {}))
        s3["enabled"] = _as_bool(s3.get("enabled", False))
        s3["verify_tls"] = _as_bool(s3.get("verify_tls", True))
        auth = dict(data.get("auth", {}))
        auth["access_token_ttl_seconds"] = int(auth.get("access_token_ttl_seconds", 86_400))
        auth["refresh_token_ttl_seconds"] = int(auth.get("refresh_token_ttl_seconds", 604_800))
        rate_limit = dict(data.get("rate_limit", {}))
        rate_limit["enabled"] = _as_bool(rate_limit.get("enabled", True))
        rate_limit["requests"] = int(rate_limit.get("requests", 30))
        rate_limit["window_seconds"] = int(rate_limit.get("window_seconds", 60))
        worker = dict(data.get("worker", {}))
        worker["poll_interval_seconds"] = int(worker.get("poll_interval_seconds", 30))
        security = data.get("security", {})
        keys = security.get("credential_encryption_keys", [])
        if isinstance(keys, str):
            keys = [key.strip() for key in keys.split(",") if key.strip()]
        return cls(
            app=AppConfig(**{**app, "cors_origins": tuple(app.get("cors_origins", []))}),
            database=DatabaseConfig(**database),
            redis=RedisConfig(**redis),
            kafka=KafkaConfig(**kafka),
            s3=S3Config(**s3),
            auth=AuthConfig(**auth),
            security=SecurityConfig(
                credential_encryption_keys=tuple(keys),
                credential_fingerprint_key=security.get("credential_fingerprint_key", ""),
            ),
            rate_limit=RateLimitConfig(**rate_limit),
            worker=WorkerConfig(**worker),
        )

    def validate_runtime_secrets(self) -> None:
        if self.app.environment not in {"production", "prod"}:
            return
        invalid: list[str] = []
        if len(self.auth.jwt_secret) < 32 or self.auth.jwt_secret == "change-me":
            invalid.append("auth.jwt_secret")
        if not self.database.password:
            invalid.append("database.password")
        if not self.redis.password:
            invalid.append("redis.password")
        if not self.security.credential_encryption_keys:
            invalid.append("security.credential_encryption_keys")
        if len(self.security.credential_fingerprint_key) < 32:
            invalid.append("security.credential_fingerprint_key")
        if "*" in self.app.cors_origins:
            invalid.append("app.cors_origins cannot contain '*' in production")
        if self.s3.enabled and not all((self.s3.key_id, self.s3.secret_key, self.s3.bucket)):
            invalid.append("s3 credentials and bucket")
        if invalid:
            raise ValueError(f"Unsafe or missing production settings: {', '.join(invalid)}")


def _default_config_path() -> Path:
    return Path(__file__).with_name("config.local.yaml")


@lru_cache
def load_settings(config_path: str | Path | None = None) -> Settings:
    selected_path: str | Path = config_path or os.getenv("CONFIG_PATH") or _default_config_path()
    path = Path(selected_path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings.from_dict(_expand_environment(raw))
