"""restricted publication approval marker (Noblen AI 3.0, Milestone 9)

Adds approvals.restricted_publication (ADR-0038,
docs/architecture/milestone-9-restricted-publication.md §13), default false.
Approvals created before Milestone 9 read false. No backfill.

Revision ID: 8e4f2a6c1b97
Revises: 5b9e1c3d7a42
Create Date: 2026-10-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e4f2a6c1b97"
down_revision: str | None = "5b9e1c3d7a42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column(
            "restricted_publication",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("approvals", "restricted_publication")
