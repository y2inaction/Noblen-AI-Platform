"""Audit logging service."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    user_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Persist an audit log entry. The caller controls the surrounding transaction."""
    entry = AuditLog(
        action=action,
        user_id=user_id,
        organization_id=organization_id,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        metadata_json=metadata,
    )
    db.add(entry)
    await db.flush()
    return entry
