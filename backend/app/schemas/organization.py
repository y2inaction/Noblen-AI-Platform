"""Organization and membership schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class OrganizationPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    currency: str
    timezone: str
    locale: str
    require_independent_approval: bool = False
    memory_retention_days: int | None = None


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    currency: str | None = Field(default=None, max_length=8)
    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=16)
    # Separation of duties for AI actions: initiators cannot approve their own runs.
    require_independent_approval: bool | None = None
    # Days an unused long-term memory is kept; null keeps memories until deleted.
    memory_retention_days: int | None = Field(default=None, ge=1, le=3650)


class AccessOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    role_name: str
    is_platform_admin: bool
    permissions: list[str]


class MemberPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role_name: str
    status: str


class MemberWithUser(MemberPublic):
    email: EmailStr | None = None
    full_name: str | None = None


class UpdateMemberRole(BaseModel):
    role_name: str
