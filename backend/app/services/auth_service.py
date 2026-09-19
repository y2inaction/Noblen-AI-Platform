"""Authentication & registration business logic.

Owns the transaction boundaries for security-sensitive flows and emits audit
logs for register/login/refresh/logout events.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.core.utils import slugify, unique_suffix
from app.models.auth import RefreshToken
from app.models.enums import MembershipStatus, RoleName, UserStatus
from app.models.membership import OrganizationMember
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import AuthResponse, TokenPair, UserPublic
from app.services.audit_service import record_audit


async def _issue_token_pair(
    db: AsyncSession, user: User, organization_id: uuid.UUID, role_name: str
) -> TokenPair:
    """Create an access/refresh pair and persist the refresh token hash."""
    claims = {"org": str(organization_id), "role": role_name}
    access = create_access_token(str(user.id), extra_claims=claims)
    refresh = create_refresh_token(str(user.id), extra_claims={"org": str(organization_id)})

    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh),
            expires_at=datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    await db.flush()
    return TokenPair(access_token=access, refresh_token=refresh)


async def _unique_slug(db: AsyncSession, name: str) -> str:
    base = slugify(name)
    slug = base
    while (await db.execute(select(Organization).where(Organization.slug == slug))).first():
        slug = f"{base}-{unique_suffix()}"
    return slug


async def register(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    organization_name: str,
    full_name: str | None = None,
    ip_address: str | None = None,
) -> AuthResponse:
    """Register a new user and create their organization (they become ADMIN)."""
    email = email.lower().strip()
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("An account with this email already exists.")

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        status=UserStatus.ACTIVE.value,
        is_email_verified=False,
    )
    db.add(user)
    await db.flush()

    org = Organization(name=organization_name, slug=await _unique_slug(db, organization_name))
    db.add(org)
    await db.flush()

    membership = OrganizationMember(
        user_id=user.id,
        organization_id=org.id,
        role_name=RoleName.ADMIN.value,
        status=MembershipStatus.ACTIVE.value,
    )
    db.add(membership)
    await db.flush()

    tokens = await _issue_token_pair(db, user, org.id, RoleName.ADMIN.value)
    await record_audit(
        db,
        action="auth.register",
        user_id=user.id,
        organization_id=org.id,
        target_type="organization",
        target_id=str(org.id),
        ip_address=ip_address,
    )
    await db.commit()

    return AuthResponse(
        user=UserPublic.model_validate(user),
        tokens=tokens,
        organization_id=org.id,
        role_name=RoleName.ADMIN.value,
    )


async def authenticate(
    db: AsyncSession, *, email: str, password: str, ip_address: str | None = None
) -> AuthResponse:
    """Verify credentials and return tokens for the user's primary organization."""
    email = email.lower().strip()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    # Constant-ish response: verify against something even if the user is missing.
    if user is None or not verify_password(password, user.hashed_password):
        raise AuthenticationError("Invalid email or password.")
    if user.status == UserStatus.DISABLED.value:
        raise AuthenticationError("This account is disabled.")

    membership = (
        await db.execute(
            select(OrganizationMember).where(OrganizationMember.user_id == user.id).limit(1)
        )
    ).scalar_one_or_none()
    if membership is None:
        raise AuthenticationError("This account has no organization access.")

    tokens = await _issue_token_pair(db, user, membership.organization_id, membership.role_name)
    await record_audit(
        db,
        action="auth.login",
        user_id=user.id,
        organization_id=membership.organization_id,
        ip_address=ip_address,
    )
    await db.commit()

    return AuthResponse(
        user=UserPublic.model_validate(user),
        tokens=tokens,
        organization_id=membership.organization_id,
        role_name=membership.role_name,
    )


async def refresh_tokens(db: AsyncSession, *, refresh_token: str) -> TokenPair:
    """Rotate a refresh token: validate, revoke the old, issue a new pair."""
    try:
        payload = decode_token(refresh_token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired refresh token.") from exc
    if payload.get("type") != "refresh":
        raise AuthenticationError("Invalid token type.")

    token_hash = hash_token(refresh_token)
    stored = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if stored is None or stored.revoked:
        raise AuthenticationError("Refresh token has been revoked.")
    if stored.expires_at.replace(tzinfo=stored.expires_at.tzinfo or UTC) < datetime.now(UTC):
        raise AuthenticationError("Refresh token has expired.")

    user = (await db.execute(select(User).where(User.id == stored.user_id))).scalar_one_or_none()
    if user is None:
        raise AuthenticationError("User no longer exists.")

    org_id = uuid.UUID(payload["org"]) if payload.get("org") else None
    membership = (
        (
            await db.execute(
                select(OrganizationMember).where(
                    OrganizationMember.user_id == user.id,
                    *([OrganizationMember.organization_id == org_id] if org_id is not None else []),
                )
            )
        )
        .scalars()
        .first()
    )
    if membership is None:
        raise AuthenticationError("Organization access has been revoked.")

    stored.revoked = True
    await db.flush()
    tokens = await _issue_token_pair(db, user, membership.organization_id, membership.role_name)
    await db.commit()
    return tokens


async def logout(db: AsyncSession, *, refresh_token: str) -> None:
    """Revoke a refresh token (idempotent)."""
    stored = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token))
        )
    ).scalar_one_or_none()
    if stored is not None and not stored.revoked:
        stored.revoked = True
        await db.commit()
