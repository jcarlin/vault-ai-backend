"""Alembic migration integration — wires Alembic into the app lifecycle."""

import asyncio
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.core.database import Base, engine

logger = structlog.get_logger()

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _alembic_cfg() -> Config:
    """Build Alembic Config with absolute paths (cwd-independent)."""
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


def _check_db_state(connection) -> tuple[bool, bool, str | None]:
    """Check database state (sync, for use with run_sync).

    Returns (has_alembic_version, has_app_tables, current_revision).
    """
    insp = inspect(connection)
    tables = insp.get_table_names()
    has_alembic = "alembic_version" in tables
    has_app_tables = "api_keys" in tables

    current_rev = None
    if has_alembic:
        result = connection.execute(text("SELECT version_num FROM alembic_version"))
        row = result.first()
        current_rev = row[0] if row else None

    return has_alembic, has_app_tables, current_rev


def _stamp_head() -> None:
    """Stamp the database at head revision without running migrations."""
    command.stamp(_alembic_cfg(), "head")


def _upgrade_head() -> None:
    """Run alembic upgrade head."""
    command.upgrade(_alembic_cfg(), "head")


async def ensure_db_migrated() -> None:
    """Ensure database schema is up to date via Alembic.

    Handles three scenarios:
    1. Fresh DB (no tables, no alembic_version): create_all + stamp head
    2. Existing DB without tracking (tables exist, no alembic_version): stamp head
    3. Alembic-tracked DB (alembic_version exists): upgrade head
    """
    async with engine.begin() as conn:
        has_alembic, has_app_tables, current_rev = await conn.run_sync(_check_db_state)

    if not has_app_tables and not has_alembic:
        # Scenario 1: Fresh database — create all tables and stamp
        logger.info("migrations_fresh_db", action="create_all_and_stamp")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await asyncio.to_thread(_stamp_head)
    elif has_app_tables and not has_alembic:
        # Scenario 2: Existing DB without Alembic tracking — stamp at head
        logger.info("migrations_existing_db", action="stamp_head")
        await asyncio.to_thread(_stamp_head)
    else:
        # Scenario 3: Alembic-tracked DB — run pending migrations
        logger.info("migrations_tracked_db", current_rev=current_rev, action="upgrade_head")
        await asyncio.to_thread(_upgrade_head)


async def run_upgrade_head() -> None:
    """Run alembic upgrade head. Used by update engine after copying new migrations."""
    await asyncio.to_thread(_upgrade_head)
