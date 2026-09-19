"""HTTP middleware: request IDs, structured access logs, secure headers,
and basic rate limiting.
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging import bind_context, clear_context, get_logger
from app.core.rate_limit import rate_limiter

logger = get_logger("http")

# Endpoints exempt from rate limiting (health probes).
_RATE_LIMIT_EXEMPT = {"/health", "/readiness"}

_SECURE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-XSS-Protection": "0",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, log the request/response, and clear context after."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        clear_context()
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        bind_context(request_id=request_id, method=request.method, path=request.url.path)
        request.state.request_id = request_id

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception("request_failed", duration_ms=duration_ms)
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        logger.info("request_completed", status_code=response.status_code, duration_ms=duration_ms)
        clear_context()
        return response


class SecureHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for header, value in _SECURE_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client sliding-window rate limiting."""

    def __init__(self, app, enabled: bool = True) -> None:  # noqa: ANN001
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.enabled or request.url.path in _RATE_LIMIT_EXEMPT:
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{request.url.path}"
        if not rate_limiter.allow(key):
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests. Please slow down.",
                    }
                },
            )
        return await call_next(request)
