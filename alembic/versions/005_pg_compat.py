"""PostgreSQL compatibility: fix Boolean server_default.

Revision ID: 005
Revises: 004
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table required for SQLite ALTER COLUMN support
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.alter_column(
            "archived",
            server_default=text("false"),
            existing_type=sa.Boolean(),
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.alter_column(
            "archived",
            server_default="0",
            existing_type=sa.Boolean(),
        )
