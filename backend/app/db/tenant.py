"""Tenant scoping utilities — the backbone of row-level isolation.

Service code MUST scope every tenant-owned query through `tenant_scoped`, which
forces an `organization_id == <active org>` filter. Cross-tenant access can only
happen by deliberately not using this helper, which is easy to spot in review and
is guarded by the tenant-isolation test suite (see tests/test_tenant_isolation.py).
"""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

from sqlalchemy import Select

# Generic over the statement so callers keep the precise row type.
_S = TypeVar("_S", bound=Select[Any])


def tenant_scoped(stmt: _S, model: type, organization_id: uuid.UUID) -> _S:
    """Add a mandatory organization_id filter to a SELECT statement.

    The model must expose an `organization_id` column (i.e. use TenantMixin).
    """
    if not hasattr(model, "organization_id"):
        raise ValueError(f"{model.__name__} is not tenant-scoped (no organization_id)")
    return stmt.where(model.organization_id == organization_id)  # type: ignore[attr-defined,return-value]
