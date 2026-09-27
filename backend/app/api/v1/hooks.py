"""Inbound webhooks (Noblen AI 3.0, M6): external systems start workflows.

`POST /hooks/workflows/{workflow_id}` with header `X-Noblen-Webhook-Token` and a
JSON object body queues a run of an active webhook-triggered workflow. There is
no user session: the token (shown once at activation, stored hashed, compared in
constant time) is the credential, and the run acts for the person who activated
the workflow. Every failure is a 404, so the endpoint reveals nothing.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.db.session import get_db
from app.workflows import service

router = APIRouter(prefix="/hooks", tags=["hooks"])


class HookAccepted(BaseModel):
    run_id: uuid.UUID
    status: str


@router.post(
    "/workflows/{workflow_id}",
    response_model=HookAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def workflow_hook(
    workflow_id: uuid.UUID,
    request: Request,
    x_noblen_webhook_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> HookAccepted:
    body = await request.body()
    if len(body) > settings.WEBHOOK_MAX_BODY_BYTES:
        raise ValidationError("The request body is too large.")
    try:
        payload = json.loads(body or b"{}")
    except ValueError as exc:
        raise ValidationError("The body must be JSON.") from exc
    if not isinstance(payload, dict):
        raise ValidationError("The body must be a JSON object.")
    run = await service.trigger_webhook(db, workflow_id, x_noblen_webhook_token, payload)
    await db.commit()
    return HookAccepted(run_id=run.id, status=run.status)
