"""AI Workforce agent templates (reference agents)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import templates as template_service
from app.api.deps import TenantContext, require_permission, user_is_platform_superuser
from app.core.exceptions import PermissionDeniedError
from app.db.session import get_db
from app.rbac.permissions import Permission, role_has_permission
from app.schemas.agent import AgentOut
from app.services.audit_service import record_audit

router = APIRouter(prefix="/agent-templates", tags=["agent-templates"])


class AgentTemplateOut(BaseModel):
    key: str
    name: str
    agent_type: str
    summary: str
    capabilities: list[str]
    planned: list[str]
    tools: dict[str, str | None]
    memory_mode: str
    version: str


class InstantiateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    activate: bool = True


def _out(t: template_service.AgentTemplate) -> AgentTemplateOut:
    return AgentTemplateOut(
        key=t.key,
        name=t.name,
        agent_type=t.agent_type,
        summary=t.summary,
        capabilities=t.capabilities,
        planned=t.planned,
        tools=t.tools,
        memory_mode=t.memory_mode,
        version=t.version,
    )


@router.get("", response_model=list[AgentTemplateOut])
async def list_templates(
    _ctx: TenantContext = Depends(require_permission(Permission.AGENT_VIEW)),
) -> list[AgentTemplateOut]:
    return [_out(t) for t in template_service.TEMPLATES.values()]


@router.post("/{key}/instantiate", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def instantiate_template(
    key: str,
    body: InstantiateRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_CREATE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    # Instantiation also binds tools and cuts/activates a version: require the
    # same rights those individual steps need.
    for needed in (Permission.TOOL_MANAGE, Permission.AGENT_MANAGE_VERSIONS):
        if not (user_is_platform_superuser(ctx.user) or role_has_permission(ctx.role_name, needed)):
            raise PermissionDeniedError(f"Your role lacks the required permission '{needed}'.")
    template = template_service.get_template(key)
    body = body or InstantiateRequest()
    agent = await template_service.instantiate(
        db, ctx.organization_id, ctx.user.id, template, name=body.name, activate=body.activate
    )
    await record_audit(
        db,
        action="agent.created_from_template",
        user_id=ctx.user.id,
        organization_id=ctx.organization_id,
        target_type="agent",
        target_id=str(agent.id),
        metadata={"template": template.key, "template_version": template.version},
    )
    await db.commit()
    await db.refresh(agent)
    return AgentOut.model_validate(agent)
