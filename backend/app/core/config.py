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

    # ---- Agent Engine (Phase 3) ----
    # Runtime guardrails against runaway/abusive agent execution.
    AGENT_MAX_ITERATIONS: int = 6
    AGENT_MAX_TOOL_CALLS: int = 10
    AGENT_MAX_RUNTIME_SECONDS: float = 120.0
    # Upper bound on how many prior messages CONVERSATION memory may load.
    AGENT_MEMORY_MAX_MESSAGES: int = 50
    # How long a pending approval stays actionable before it expires.
    AGENT_APPROVAL_TTL_SECONDS: int = 86400
    # Configuration/input size limits.
    AGENT_MAX_SYSTEM_INSTRUCTIONS_CHARS: int = 20000
    AGENT_MAX_INPUT_CHARS: int = 20000

    # ---- Knowledge + RAG (Phase 4) ----
    # Embedding model config. The vector column dimension is fixed platform-wide
    # (KNOWLEDGE_EMBEDDING_DIMENSION) and must match the configured model.
    KNOWLEDGE_EMBEDDING_PROVIDER: str = "openai"
    KNOWLEDGE_EMBEDDING_MODEL: str = "text-embedding-3-small"
    KNOWLEDGE_EMBEDDING_DIMENSION: int = 1536
    # Ingestion limits (guard memory + cost).
    MAX_DOCUMENT_SIZE_MB: int = 20
    MAX_DOCUMENT_TEXT_LENGTH: int = 2_000_000
    MAX_CHUNKS_PER_DOCUMENT: int = 1000
    # Chunking (character-based, paragraph-aware).
    KNOWLEDGE_CHUNK_SIZE: int = 1200
    KNOWLEDGE_CHUNK_OVERLAP: int = 150
    # Retrieval.
    KNOWLEDGE_DEFAULT_TOP_K: int = 5
    KNOWLEDGE_MAX_TOP_K: int = 20
    # Minimum cosine similarity (0..1) for a chunk to be returned.
    KNOWLEDGE_DEFAULT_SIMILARITY_THRESHOLD: float = 0.0
    # Local document storage root (dev/test); swap for object storage in prod.
    KNOWLEDGE_STORAGE_DIR: str = "./storage/knowledge"
    # Privacy: never log document content by default.
    KNOWLEDGE_LOG_CONTENT: bool = False

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
