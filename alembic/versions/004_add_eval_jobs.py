"""Add eval_jobs table for Epic 17 evaluation & benchmarking.

Revision ID: 004
Revises: 003
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eval_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("adapter_id", sa.String(36), sa.ForeignKey("adapters.id"), nullable=True),
        sa.Column("dataset_id", sa.String(255), nullable=False),
        sa.Column("dataset_type", sa.String(20), nullable=False, server_default="builtin"),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("results_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("total_examples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("examples_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("eval_jobs")
