"""Shared API dependencies: authentication, active-tenant resolution, RBAC.

Permissions are enforced here on the server. The client cannot grant itself
access by sending a header — every membership and role is validated against the
database (ARCHITECTURE.md §5, §4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.logging import bind_context
from app.core.security import decode_token
from app.db.session import get_db
from app.models.enums import MembershipStatus
from app.models.membership import OrganizationMember
from app.models.user import User
from app.rbac.permissions import role_has_permission

_bearer = HTTPBearer(auto_error=False)


@dataclass
class TenantContext:
    """The authenticated caller's active tenant + role for this request."""

    user: User
    organization_id: uuid.UUID
    role_name: str


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token.")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc
    if payload.get("type") != "access":
        raise AuthenticationError("Invalid token type.")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Malformed token subject.") from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise AuthenticationError("User no longer exists.")
    bind_context(user_id=str(user.id))
    return user


async def get_tenant_context(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
) -> TenantContext:
    """Resolve and validate the caller's active organization membership.

    The active org comes from the X-Organization-Id header if present, else from
    the access-token `org` claim. Membership is always re-validated in the DB.
    """
    org_id_raw = x_organization_id
    if org_id_raw is None:
        try:
            payload = decode_token(request.headers["authorization"].split(" ", 1)[1])
            org_id_raw = payload.get("org")
        except (KeyError, IndexError, jwt.PyJWTError):
            org_id_raw = None
    if not org_id_raw:
        raise AuthenticationError("No active organization for this request.")

    try:
        organization_id = uuid.UUID(str(org_id_raw))
    except ValueError as exc:
        raise AuthenticationError("Invalid organization id.") from exc

    membership = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user.id,
                OrganizationMember.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None or membership.status != MembershipStatus.ACTIVE.value:
        # Do not leak whether the org exists — treat as forbidden.
        raise PermissionDeniedError("You do not have access to this organization.")

    bind_context(organization_id=str(organization_id))
    return TenantContext(user=user, organization_id=organization_id, role_name=membership.role_name)


def require_permission(permission: str):
    """Dependency factory enforcing a permission for the active tenant/role."""

    async def _checker(
        ctx: TenantContext = Depends(get_tenant_context),
    ) -> TenantContext:
        if user_is_platform_superuser(ctx.user):
            return ctx
        if not role_has_permission(ctx.role_name, permission):
            raise PermissionDeniedError(
                f"Your role '{ctx.role_name}' lacks the required permission '{permission}'."
            )
        return ctx

    return _checker


def user_is_platform_superuser(user: User) -> bool:
    return bool(user.is_superuser)
