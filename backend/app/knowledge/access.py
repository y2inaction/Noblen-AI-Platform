"""Knowledge access control within an organization (Noblen AI 3.0, M3).

Tenancy (organization_id) is the outer wall; this module decides *who inside the
organization* may read a knowledge base or document:

- A knowledge base is readable when its visibility is ORGANIZATION, or the reader
  created it, or a grant names the reader's user id or current role.
- A document is readable when its knowledge base is readable AND (the document
  inherits visibility, or the reader created it, or a document grant matches).
- Readers holding `knowledge:read_all` (org admins) or platform superusers bypass
  the checks — but never the tenant boundary.

Rules are expressed as SQL predicates so retrieval filters **inside the query**
(before ranking and LIMIT), never after. Agents search with their initiator's
principal, intersected with the knowledge bases attached to the agent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, exists, false, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.db.tenant import tenant_scoped
from app.models.enums import (
    DocumentVisibility,
    GrantPrincipalType,
    KnowledgeResourceType,
    KnowledgeVisibility,
    MembershipStatus,
    RoleName,
)
from app.models.knowledge import KnowledgeAccessGrant, KnowledgeBase, KnowledgeDocument
from app.models.membership import OrganizationMember
from app.models.user import User
from app.rbac.permissions import Permission, role_has_permission


@dataclass(frozen=True)
class Principal:
    """Who is reading. `role` is the reader's *current* role in the organization."""

    organization_id: uuid.UUID
    user_id: uuid.UUID | None
    role: str | None
    read_all: bool = False

    @property
    def is_member(self) -> bool:
        return self.read_all or (self.user_id is not None and self.role is not None)


async def resolve_principal(
    db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None
) -> Principal:
    """Build a principal from the database (never from a token or model output)."""
    if user_id is None:
        return Principal(organization_id, None, None)
    user = await db.get(User, user_id)
    if user is not None and user.is_superuser:
        return Principal(organization_id, user_id, RoleName.SUPER_ADMIN.value, read_all=True)
    member = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
                OrganizationMember.status == MembershipStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        return Principal(organization_id, user_id, None)  # reads nothing
    return Principal(
        organization_id,
        user_id,
        member.role_name,
        read_all=role_has_permission(member.role_name, Permission.KNOWLEDGE_READ_ALL),
    )


def _grant_matches(principal: Principal, resource_type: str, resource_id: Any) -> Any:
    candidates = []
    if principal.user_id is not None:
        candidates.append(
            and_(
                KnowledgeAccessGrant.principal_type == GrantPrincipalType.USER.value,
                KnowledgeAccessGrant.principal == str(principal.user_id),
            )
        )
    if principal.role is not None:
        candidates.append(
            and_(
                KnowledgeAccessGrant.principal_type == GrantPrincipalType.ROLE.value,
                KnowledgeAccessGrant.principal == principal.role,
            )
        )
    if not candidates:
        return false()
    return exists().where(
        KnowledgeAccessGrant.organization_id == principal.organization_id,
        KnowledgeAccessGrant.resource_type == resource_type,
        KnowledgeAccessGrant.resource_id == resource_id,
        or_(*candidates),
    )


def knowledge_base_readable(principal: Principal) -> ColumnElement[bool]:
    """SQL predicate over KnowledgeBase rows."""
    if principal.read_all:
        return true()
    if not principal.is_member:
        return false()
    return or_(
        KnowledgeBase.visibility == KnowledgeVisibility.ORGANIZATION.value,
        KnowledgeBase.created_by == principal.user_id,
        _grant_matches(principal, KnowledgeResourceType.KNOWLEDGE_BASE.value, KnowledgeBase.id),
    )


def document_readable(principal: Principal) -> ColumnElement[bool]:
    """SQL predicate over KnowledgeDocument rows (document-level rule only).

    Combine with `knowledge_base_readable` via a join on the document's base.
    """
    if principal.read_all:
        return true()
    if not principal.is_member:
        return false()
    return or_(
        KnowledgeDocument.visibility == DocumentVisibility.INHERIT.value,
        KnowledgeDocument.created_by == principal.user_id,
        _grant_matches(principal, KnowledgeResourceType.DOCUMENT.value, KnowledgeDocument.id),
    )


def readable_documents_query(principal: Principal) -> Any:
    """SELECT of document ids the principal may read (tenant-scoped)."""
    return (
        select(KnowledgeDocument.id)
        .join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.knowledge_base_id)
        .where(
            KnowledgeDocument.organization_id == principal.organization_id,
            knowledge_base_readable(principal),
            document_readable(principal),
        )
    )


async def can_read_knowledge_base(db: AsyncSession, principal: Principal, kb_id: uuid.UUID) -> bool:
    stmt = tenant_scoped(select(KnowledgeBase.id), KnowledgeBase, principal.organization_id).where(
        KnowledgeBase.id == kb_id, knowledge_base_readable(principal)
    )
    return (await db.execute(stmt)).first() is not None


async def can_read_document(db: AsyncSession, principal: Principal, doc_id: uuid.UUID) -> bool:
    stmt = readable_documents_query(principal).where(KnowledgeDocument.id == doc_id)
    return (await db.execute(stmt)).first() is not None


# --------------------------------------------------------------------------- #
# Grants
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GrantSpec:
    principal_type: str
    principal: str


async def _validate_grants(
    db: AsyncSession, organization_id: uuid.UUID, grants: list[GrantSpec]
) -> list[GrantSpec]:
    valid_roles = {r.value for r in RoleName}
    cleaned: list[GrantSpec] = []
    for grant in grants:
        if grant.principal_type == GrantPrincipalType.ROLE.value:
            if grant.principal not in valid_roles:
                raise ValidationError(f"Unknown role '{grant.principal}'.")
        elif grant.principal_type == GrantPrincipalType.USER.value:
            try:
                user_id = uuid.UUID(grant.principal)
            except ValueError as exc:
                raise ValidationError("User grants need a user id.") from exc
            member = (
                await db.execute(
                    select(OrganizationMember.id).where(
                        OrganizationMember.organization_id == organization_id,
                        OrganizationMember.user_id == user_id,
                    )
                )
            ).first()
            if member is None:
                raise ValidationError("Grants can only name members of this organization.")
            grant = GrantSpec(grant.principal_type, str(user_id))
        else:
            raise ValidationError(f"Unknown principal type '{grant.principal_type}'.")
        if grant not in cleaned:
            cleaned.append(grant)
    return cleaned


async def list_grants(
    db: AsyncSession, organization_id: uuid.UUID, resource_type: str, resource_id: uuid.UUID
) -> list[KnowledgeAccessGrant]:
    rows = await db.execute(
        tenant_scoped(select(KnowledgeAccessGrant), KnowledgeAccessGrant, organization_id)
        .where(
            KnowledgeAccessGrant.resource_type == resource_type,
            KnowledgeAccessGrant.resource_id == resource_id,
        )
        .order_by(KnowledgeAccessGrant.principal_type, KnowledgeAccessGrant.principal)
    )
    return list(rows.scalars().all())


async def replace_grants(
    db: AsyncSession,
    organization_id: uuid.UUID,
    resource_type: str,
    resource_id: uuid.UUID,
    grants: list[GrantSpec],
    *,
    created_by: uuid.UUID | None,
) -> list[KnowledgeAccessGrant]:
    cleaned = await _validate_grants(db, organization_id, grants)
    await db.execute(
        delete(KnowledgeAccessGrant).where(
            KnowledgeAccessGrant.organization_id == organization_id,
            KnowledgeAccessGrant.resource_type == resource_type,
            KnowledgeAccessGrant.resource_id == resource_id,
        )
    )
    for grant in cleaned:
        db.add(
            KnowledgeAccessGrant(
                organization_id=organization_id,
                resource_type=resource_type,
                resource_id=resource_id,
                principal_type=grant.principal_type,
                principal=grant.principal,
                created_by=created_by,
            )
        )
    await db.flush()
    return await list_grants(db, organization_id, resource_type, resource_id)
