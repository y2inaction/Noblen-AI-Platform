"""Tenant scoping utilities — the backbone of row-level isolation.

Service code MUST scope every tenant-owned query through `tenant_scoped`, which
forces an `organization_id == <active org>` filter. Cross-tenant access can only
happen by deliberately not using this helper, which is easy to spot in review and
is guarded by the tenant-isolation test suite (see tests/test_tenant_isolation.py).
"""

from __future__ import annotations

import uuid
from typing import TypeVar

from sqlalchemy import Select

_M = TypeVar("_M")


def tenant_scoped(stmt: Select, model: type, organization_id: uuid.UUID) -> Select:
    """Add a mandatory organization_id filter to a SELECT statement.

    The model must expose an `organization_id` column (i.e. use TenantMixin).
    """
    if not hasattr(model, "organization_id"):
        raise ValueError(f"{model.__name__} is not tenant-scoped (no organization_id)")
    return stmt.where(model.organization_id == organization_id)
