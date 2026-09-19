"""Organization & membership endpoints (tenant-scoped, RBAC-gated)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.db.session import get_db
from app.models.enums import RoleName
from app.models.membership import OrganizationMember
from app.models.organization import Organization
from app.models.user import User
from app.rbac.permissions import Permission
from app.schemas.organization import (
    MemberWithUser,
    OrganizationPublic,
    OrganizationUpdate,
    UpdateMemberRole,
)
from app.services.audit_service import record_audit

router = APIRouter(prefix="/organizations", tags=["organizations"])

_VALID_ROLES = {r.value for r in RoleName}


async def _get_org(db: AsyncSession, organization_id: uuid.UUID) -> Organization:
    org = (
        await db.execute(select(Organization).where(Organization.id == organization_id))
    ).scalar_one_or_none()
    if org is None:
        raise NotFoundError("Organization not found.")
    return org


@router.get("/current", response_model=OrganizationPublic)
async def get_current_organization(
    ctx: TenantContext = Depends(require_permission(Permission.ORG_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> OrganizationPublic:
    org = await _get_org(db, ctx.organization_id)
    return OrganizationPublic.model_validate(org)


@router.patch("/current", response_model=OrganizationPublic)
async def update_current_organization(
    body: OrganizationUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.ORG_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> OrganizationPublic:
    org = await _get_org(db, ctx.organization_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(org, field, value)
    await record_audit(
        db,
        action="org.updated",
        user_id=ctx.user.id,
        organization_id=org.id,
        target_type="organization",
        target_id=str(org.id),
    )
    await db.commit()
    await db.refresh(org)
    return OrganizationPublic.model_validate(org)


@router.get("/current/members", response_model=list[MemberWithUser])
async def list_members(
    ctx: TenantContext = Depends(require_permission(Permission.MEMBER_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> list[MemberWithUser]:
    # Tenant-scoped: only members of the caller's active organization.
    rows = (
        await db.execute(
            select(OrganizationMember, User)
            .join(User, User.id == OrganizationMember.user_id)
            .where(OrganizationMember.organization_id == ctx.organization_id)
        )
    ).all()
    result: list[MemberWithUser] = []
    for member, user in rows:
        item = MemberWithUser.model_validate(member)
        item.email = user.email
        item.full_name = user.full_name
        result.append(item)
    return result


@router.patch("/current/members/{member_id}/role", response_model=MemberWithUser)
async def update_member_role(
    member_id: uuid.UUID,
    body: UpdateMemberRole,
    ctx: TenantContext = Depends(require_permission(Permission.MEMBER_UPDATE_ROLE)),
    db: AsyncSession = Depends(get_db),
) -> MemberWithUser:
    if body.role_name not in _VALID_ROLES:
        raise ValidationError(f"Unknown role '{body.role_name}'.")

    # Tenant-scoped lookup: the member must belong to the caller's org.
    member = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.id == member_id,
                OrganizationMember.organization_id == ctx.organization_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise NotFoundError("Member not found in this organization.")

    old_role = member.role_name
    member.role_name = body.role_name
    await record_audit(
        db,
        action="member.role_changed",
        user_id=ctx.user.id,
        organization_id=ctx.organization_id,
        target_type="organization_member",
        target_id=str(member.id),
        metadata={"from": old_role, "to": body.role_name},
    )
    await db.commit()

    user = (await db.execute(select(User).where(User.id == member.user_id))).scalar_one_or_none()
    item = MemberWithUser.model_validate(member)
    if user is not None:
        item.email = user.email
        item.full_name = user.full_name
    return item
