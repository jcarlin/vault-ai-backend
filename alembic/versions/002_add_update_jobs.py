"""Add update_jobs table for Epic 10 update mechanism.

Revision ID: 002
Revises: 001
Create Date: 2026-02-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "update_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("bundle_version", sa.String(50), nullable=False),
        sa.Column("from_version", sa.String(50), nullable=False),
        sa.Column("bundle_path", sa.String(1000), nullable=True),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_step", sa.String(100), nullable=True),
        sa.Column("steps_json", sa.Text(), nullable=True),
        sa.Column("log_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("components_json", sa.Text(), nullable=True),
        sa.Column("backup_path", sa.String(1000), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("update_jobs")
