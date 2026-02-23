"""Add uptime_events table for service uptime tracking.

Revision ID: 007
Revises: 006
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "uptime_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("service_name", sa.String(100), nullable=False, index=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("timestamp", sa.DateTime(), server_default=sa.func.now(), index=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("uptime_events")
