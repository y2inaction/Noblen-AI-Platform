"""restricted publication workflow step marker (Noblen AI 3.0, Milestone 9)

Adds workflow_step_runs.restricted_publication (ADR-0038,
docs/architecture/milestone-9-restricted-publication.md §13), default false.
Step runs recorded before Milestone 9 read false. No backfill.

Revision ID: c3d5e7f9a1b2
Revises: 8e4f2a6c1b97
Create Date: 2026-10-02 00:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d5e7f9a1b2"
down_revision: str | None = "8e4f2a6c1b97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_step_runs",
        sa.Column(
            "restricted_publication",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_step_runs", "restricted_publication")
