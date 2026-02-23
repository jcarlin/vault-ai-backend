import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings


class Base(DeclarativeBase):
    pass


# ── Rev 1 ────────────────────────────────────────────────────────────────────


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(255))
    scope: Mapped[str] = mapped_column(String(20), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )


# ── Rev 2: Users ─────────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    role: Mapped[str] = mapped_column(String(20), default="user")
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_active: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ldap_dn: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    auth_source: Mapped[str] = mapped_column(String(20), default="local")


# ── Rev 2: Conversations ─────────────────────────────────────────────────────


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    model_id: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    thinking_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    thinking_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ── Rev 2: Training Jobs ─────────────────────────────────────────────────────


class TrainingJob(Base):
    __tablename__ = "training_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    model: Mapped[str] = mapped_column(String(255))
    dataset: Mapped[str] = mapped_column(String(500))
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    adapter_type: Mapped[str] = mapped_column(String(20), default="lora")
    lora_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    adapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ── Epic 16: Adapters ──────────────────────────────────────────────────────


class Adapter(Base):
    __tablename__ = "adapters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    base_model: Mapped[str] = mapped_column(String(255))
    adapter_type: Mapped[str] = mapped_column(String(20), default="lora")
    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready/active/failed
    path: Mapped[str] = mapped_column(String(1000))
    training_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("training_jobs.id"), nullable=True
    )
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    activated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


# ── Epic 17: Eval Jobs ─────────────────────────────────────────────────────


class EvalJob(Base):
    __tablename__ = "eval_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    model_id: Mapped[str] = mapped_column(String(255))
    adapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("adapters.id"), nullable=True
    )
    dataset_id: Mapped[str] = mapped_column(String(255))
    dataset_type: Mapped[str] = mapped_column(String(20), default="builtin")
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_examples: Mapped[int] = mapped_column(Integer, default=0)
    examples_completed: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ── Rev 2: Audit Log ─────────────────────────────────────────────────────────


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    action: Mapped[str] = mapped_column(String(50))
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user_key_prefix: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Rev 2: System Config ─────────────────────────────────────────────────────


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ── Epic 14: LDAP Group Mapping ─────────────────────────────────────────────


class LdapGroupMapping(Base):
    __tablename__ = "ldap_group_mappings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ldap_group_dn: Mapped[str] = mapped_column(String(1000), unique=True)
    vault_role: Mapped[str] = mapped_column(String(20), default="user")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ── Epic 9: Quarantine ──────────────────────────────────────────────────────


class QuarantineJob(Base):
    __tablename__ = "quarantine_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/scanning/completed/failed
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    files_completed: Mapped[int] = mapped_column(Integer, default=0)
    files_flagged: Mapped[int] = mapped_column(Integer, default=0)
    files_clean: Mapped[int] = mapped_column(Integer, default=0)
    source_type: Mapped[str] = mapped_column(String(20), default="upload")  # upload/usb_path/model_import
    submitted_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class QuarantineFile(Base):
    __tablename__ = "quarantine_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quarantine_jobs.id"), index=True
    )
    original_filename: Mapped[str] = mapped_column(String(500))
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/scanning/clean/held/approved/rejected
    current_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    risk_severity: Mapped[str] = mapped_column(String(20), default="none")  # none/low/medium/high/critical
    findings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    quarantine_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    sanitized_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    destination_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ── Epic 10: Update Jobs ─────────────────────────────────────────────────────


class UpdateJob(Base):
    __tablename__ = "update_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending → verifying → backing_up → extracting → migrating →
    # staging → loading_containers → restarting → health_checking →
    # completed | failed | rolled_back
    bundle_version: Mapped[str] = mapped_column(String(50))
    from_version: Mapped[str] = mapped_column(String(50))
    bundle_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str | None] = mapped_column(String(100), nullable=True)
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    components_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    backup_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ── Engine & Session ──────────────────────────────────────────────────────────

engine = create_async_engine(settings.vault_db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Ensure database schema is up to date via Alembic migrations."""
    from app.core.migrations import ensure_db_migrated

    await ensure_db_migrated()


async def close_db() -> None:
    """Dispose of the engine."""
    await engine.dispose()


async def get_session() -> AsyncSession:
    """Yield a database session (for use as FastAPI dependency)."""
    async with async_session() as session:
        yield session
