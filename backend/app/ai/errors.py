"""Normalized, provider-independent AI errors.

Provider adapters map vendor exceptions into these so the application layer never
needs to understand SDK-specific exception types. Each error declares whether it
is safe to retry.
"""

from __future__ import annotations


class AIError(Exception):
    """Base class for all AI Core errors."""

    error_code: str = "ai_error"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.status_code = status_code

    def __str__(self) -> str:
        prefix = f"[{self.provider}] " if self.provider else ""
        return f"{prefix}{self.message}"


class AIAuthenticationError(AIError):
    """Bad or missing API key. Never retried."""

    error_code = "ai_authentication_error"
    retryable = False


class AIInvalidRequestError(AIError):
    """Malformed/invalid request (e.g. bad params). Never retried."""

    error_code = "ai_invalid_request_error"
    retryable = False


class AIRateLimitError(AIError):
    """Provider rate limit hit. Retried with backoff."""

    error_code = "ai_rate_limit_error"
    retryable = True


class AITimeoutError(AIError):
    """Request exceeded the configured timeout. Retried."""

    error_code = "ai_timeout_error"
    retryable = True


class AIProviderUnavailableError(AIError):
    """Provider is temporarily unavailable (5xx / network). Retried."""

    error_code = "ai_provider_unavailable_error"
    retryable = True


class AIQuotaError(AIError):
    """Permanent quota/billing failure. Never retried."""

    error_code = "ai_quota_error"
    retryable = False


class AIUnknownProviderError(AIError):
    """Requested provider is not registered/configured. Never retried."""

    error_code = "ai_unknown_provider_error"
    retryable = False


class AIUnknownError(AIError):
    """Fallback for unmapped provider errors. Not retried by default."""

    error_code = "ai_unknown_error"
    retryable = False
