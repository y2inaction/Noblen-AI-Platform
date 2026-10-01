"""restricted publication setting (Noblen AI 3.0, Milestone 9)

Adds organizations.require_approval_to_publish_restricted (ADR-0038,
docs/architecture/milestone-9-restricted-publication.md §4), default false.
Existing organizations get false, so behavior is unchanged. No backfill.

Revision ID: 5b9e1c3d7a42
Revises: 810da20ca5ee
Create Date: 2026-10-01 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b9e1c3d7a42"
down_revision: str | None = "810da20ca5ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "require_approval_to_publish_restricted",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "require_approval_to_publish_restricted")
