"""run provenance (Noblen AI 3.0, Milestone 8)

Adds provenance columns (ADR-0037, docs/architecture/milestone-8-provenance.md §4):
- agent_runs.sources, sources_truncated, acting_role
- workflow_step_runs.sources, sources_truncated
- workflow_runs.sources, sources_truncated, acting_role

`sources` stays NULL for existing rows: provenance recorded before M8 is unknown
and fails closed. `sources_truncated` defaults to false. No data is backfilled.

Revision ID: 810da20ca5ee
Revises: 08100b04d46f
Create Date: 2026-09-28 00:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "810da20ca5ee"
down_revision: str | None = "08100b04d46f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUN_TABLES = ("agent_runs", "workflow_step_runs", "workflow_runs")
_ROLE_TABLES = ("agent_runs", "workflow_runs")


def upgrade() -> None:
    for table in _RUN_TABLES:
        op.add_column(table, sa.Column("sources", sa.JSON(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "sources_truncated", sa.Boolean(), server_default=sa.false(), nullable=False
            ),
        )
    for table in _ROLE_TABLES:
        op.add_column(table, sa.Column("acting_role", sa.String(length=32), nullable=True))


def downgrade() -> None:
    for table in _ROLE_TABLES:
        op.drop_column(table, "acting_role")
    for table in _RUN_TABLES:
        op.drop_column(table, "sources_truncated")
        op.drop_column(table, "sources")
