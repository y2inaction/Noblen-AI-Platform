"""AI-specific FastAPI dependencies: per-organization rate limiting.

Reuses the platform's sliding-window limiter, keyed by organization so limits are
tenant-scoped and can later be driven by subscription plan.
"""

from __future__ import annotations

from fastapi import Depends

from app.api.deps import TenantContext, get_tenant_context
from app.core.config import settings
from app.core.exceptions import RateLimitError
from app.core.rate_limit import SlidingWindowRateLimiter

ai_rate_limiter = SlidingWindowRateLimiter(
    max_requests=settings.AI_RATE_LIMIT_REQUESTS,
    window_seconds=settings.AI_RATE_LIMIT_WINDOW_SECONDS,
)


async def enforce_ai_rate_limit(
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    if not settings.RATE_LIMIT_ENABLED:
        return
    if not ai_rate_limiter.allow(f"ai:{ctx.organization_id}"):
        raise RateLimitError("AI request rate limit exceeded for this organization.")
