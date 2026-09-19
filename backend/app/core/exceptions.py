"""Typed application exceptions and their HTTP mapping.

Errors are never silently swallowed; each carries a stable machine-readable
code plus a user-safe message.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base application error with an HTTP status and a stable error code."""

    status_code: int = 400
    error_code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": {"code": self.error_code, "message": self.message}}
        if self.details is not None:
            body["error"]["details"] = self.details
        return body


class AuthenticationError(AppError):
    status_code = 401
    error_code = "authentication_error"


class PermissionDeniedError(AppError):
    status_code = 403
    error_code = "permission_denied"


class NotFoundError(AppError):
    status_code = 404
    error_code = "not_found"


class ConflictError(AppError):
    status_code = 409
    error_code = "conflict"


class ValidationError(AppError):
    status_code = 422
    error_code = "validation_error"


class RateLimitError(AppError):
    status_code = 429
    error_code = "rate_limited"
