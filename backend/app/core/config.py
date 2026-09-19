"""Application configuration, loaded from environment variables.

No secrets are hard-coded. Every value can be overridden via the environment
(or a local `.env` file that is never committed).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Core ----
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    PROJECT_NAME: str = "Noblen AI Platform"
    API_V1_PREFIX: str = "/api/v1"
    BACKEND_CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # ---- Security ----
    JWT_SECRET: str = "dev-only-insecure-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    # ---- Database ----
    # Default is an in-memory async SQLite DB so the app/tests can boot with no
    # external services. Production overrides this with a PostgreSQL DSN.
    DATABASE_URL: str = "sqlite+aiosqlite:///./noblen_dev.db"

    # ---- Redis ----
    REDIS_URL: str = "redis://localhost:6379/0"

    # ---- Rate limiting ----
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # ---- AI providers (Phase 2) ----
    ANTHROPIC_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    DEFAULT_AI_PROVIDER: str = "anthropic"
    DEFAULT_AI_MODEL: str = "claude-sonnet-4-5"

    # ---- Localisation defaults (per-org configurable at runtime) ----
    DEFAULT_CURRENCY: str = "NGN"
    DEFAULT_TIMEZONE: str = "Africa/Lagos"
    DEFAULT_LOCALE: str = "en"

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
