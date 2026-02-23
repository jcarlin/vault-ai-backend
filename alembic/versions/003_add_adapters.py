"""Add adapters table and adapter columns to training_jobs for Epic 16.

Revision ID: 003
Revises: 002
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add adapter columns to training_jobs
    op.add_column("training_jobs", sa.Column("adapter_type", sa.String(20), server_default="lora", nullable=False))
    op.add_column("training_jobs", sa.Column("lora_config_json", sa.Text(), nullable=True))
    op.add_column("training_jobs", sa.Column("adapter_id", sa.String(36), nullable=True))

    # Create adapters table
    op.create_table(
        "adapters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("base_model", sa.String(255), nullable=False),
        sa.Column("adapter_type", sa.String(20), nullable=False, server_default="lora"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ready"),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("training_job_id", sa.String(36), sa.ForeignKey("training_jobs.id"), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("adapters")
    op.drop_column("training_jobs", "adapter_id")
    op.drop_column("training_jobs", "lora_config_json")
    op.drop_column("training_jobs", "adapter_type")
