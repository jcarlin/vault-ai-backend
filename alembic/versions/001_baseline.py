"""Baseline schema — snapshot of all existing tables.

Revision ID: 001
Revises: None
Create Date: 2026-02-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_active", sa.DateTime(), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("ldap_dn", sa.String(1000), nullable=True),
        sa.Column("auth_source", sa.String(20), nullable=False, server_default="local"),
    )

    # ── api_keys ─────────────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(20), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    # ── conversations ────────────────────────────────────────────────────────
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
    )

    # ── messages ─────────────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("thinking_content", sa.Text(), nullable=True),
        sa.Column("thinking_duration_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    # ── training_jobs ────────────────────────────────────────────────────────
    op.create_table(
        "training_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("dataset", sa.String(500), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.Column("resource_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── audit_log ────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("method", sa.String(10), nullable=True),
        sa.Column("path", sa.String(500), nullable=True),
        sa.Column("user_key_prefix", sa.String(20), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
    )
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])

    # ── system_config ────────────────────────────────────────────────────────
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── ldap_group_mappings ──────────────────────────────────────────────────
    op.create_table(
        "ldap_group_mappings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ldap_group_dn", sa.String(1000), nullable=False, unique=True),
        sa.Column("vault_role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── quarantine_jobs ──────────────────────────────────────────────────────
    op.create_table(
        "quarantine_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("total_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_flagged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_clean", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_type", sa.String(20), nullable=False, server_default="upload"),
        sa.Column("submitted_by", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    # ── quarantine_files ─────────────────────────────────────────────────────
    op.create_table(
        "quarantine_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("quarantine_jobs.id"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mime_type", sa.String(255), nullable=True),
        sa.Column("sha256_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("current_stage", sa.String(50), nullable=True),
        sa.Column("risk_severity", sa.String(20), nullable=False, server_default="none"),
        sa.Column("findings_json", sa.Text(), nullable=True),
        sa.Column("quarantine_path", sa.String(1000), nullable=True),
        sa.Column("sanitized_path", sa.String(1000), nullable=True),
        sa.Column("destination_path", sa.String(1000), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(20), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_quarantine_files_job_id", "quarantine_files", ["job_id"])


def downgrade() -> None:
    op.drop_table("quarantine_files")
    op.drop_table("quarantine_jobs")
    op.drop_table("ldap_group_mappings")
    op.drop_table("system_config")
    op.drop_table("audit_log")
    op.drop_table("training_jobs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("api_keys")
    op.drop_table("users")
