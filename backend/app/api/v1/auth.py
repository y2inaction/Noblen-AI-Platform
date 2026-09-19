"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.membership import OrganizationMember
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    MembershipPublic,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserPublic,
)
from app.schemas.common import Message
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> AuthResponse:
    return await auth_service.register(
        db,
        email=body.email,
        password=body.password,
        organization_name=body.organization_name,
        full_name=body.full_name,
        ip_address=_client_ip(request),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> AuthResponse:
    return await auth_service.authenticate(
        db, email=body.email, password=body.password, ip_address=_client_ip(request)
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    return await auth_service.refresh_tokens(db, refresh_token=body.refresh_token)


@router.post("/logout", response_model=Message)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> Message:
    await auth_service.logout(db, refresh_token=body.refresh_token)
    return Message(message="Logged out.")


@router.get("/me", response_model=MeResponse)
async def me(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> MeResponse:
    memberships = (
        (await db.execute(select(OrganizationMember).where(OrganizationMember.user_id == user.id)))
        .scalars()
        .all()
    )
    return MeResponse(
        user=UserPublic.model_validate(user),
        memberships=[MembershipPublic.model_validate(m) for m in memberships],
    )
