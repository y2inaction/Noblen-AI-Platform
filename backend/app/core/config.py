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

    # ---- AI Core (Phase 2) ----
    # Provider secrets — backend only, never exposed to the frontend.
    ANTHROPIC_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None

    # Default provider/model selection (never hard-code model names in logic).
    AI_DEFAULT_PROVIDER: str = "anthropic"
    AI_DEFAULT_MODEL: str = "claude-sonnet-4-5"
    AI_DEFAULT_EMBEDDING_PROVIDER: str = "openai"
    AI_DEFAULT_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Resilience.
    AI_REQUEST_TIMEOUT_SECONDS: float = 60.0
    AI_MAX_RETRIES: int = 2
    AI_RETRY_BASE_DELAY_SECONDS: float = 0.5

    # Per-organization AI rate limiting (a safe platform default; per-plan later).
    AI_RATE_LIMIT_REQUESTS: int = 60
    AI_RATE_LIMIT_WINDOW_SECONDS: int = 60

    # Privacy: never persist/log full prompts or responses unless explicitly enabled.
    AI_LOG_PROMPTS: bool = False

    # Optional JSON override for the model-pricing registry (see app/ai/pricing.py).
    # Example: {"openai:gpt-4o-mini": {"input_per_million": 0.15, "output_per_million": 0.6}}
    AI_PRICING_OVERRIDES_JSON: str | None = None

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
