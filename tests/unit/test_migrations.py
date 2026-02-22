"""Tests for Alembic migration integration."""

from unittest.mock import patch

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import Base
from app.core.migrations import _BACKEND_ROOT, ensure_db_migrated


# ── Helpers ───────────────────────────────────────────────────────────────────


def _test_cfg(connection):
    """Alembic Config wired to a sync connection for isolated tests."""
    from alembic.config import Config

    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.attributes["connection"] = connection
    return cfg


# ── Migration Chain Tests (sync, isolated SQLite) ────────────────────────────


class TestMigrationChain:
    """Test Alembic migration files produce the correct schema."""

    def test_upgrade_to_head_creates_all_tables(self, tmp_path):
        """All 11 app tables + alembic_version are created at head."""
        engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
        with engine.begin() as conn:
            command.upgrade(_test_cfg(conn), "head")

        with engine.begin() as conn:
            tables = set(inspect(conn).get_table_names())

        expected = {
            "users", "api_keys", "conversations", "messages",
            "training_jobs", "audit_log", "system_config",
            "ldap_group_mappings", "quarantine_jobs", "quarantine_files",
            "update_jobs", "alembic_version",
        }
        assert tables == expected
        engine.dispose()

    def test_upgrade_then_downgrade_to_base(self, tmp_path):
        """Downgrade to base leaves only alembic_version (or empty)."""
        engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
        with engine.begin() as conn:
            command.upgrade(_test_cfg(conn), "head")
        with engine.begin() as conn:
            command.downgrade(_test_cfg(conn), "base")

        with engine.begin() as conn:
            tables = set(inspect(conn).get_table_names())

        # alembic_version may remain after downgrade to base
        assert tables <= {"alembic_version"}
        engine.dispose()

    def test_head_revision_is_002(self, tmp_path):
        """Current migration head is revision 002."""
        engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
        with engine.begin() as conn:
            command.upgrade(_test_cfg(conn), "head")

        with engine.begin() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()

        assert row is not None
        assert row[0] == "002"
        engine.dispose()

    def test_migration_schema_matches_create_all(self, tmp_path):
        """Migration-created schema matches Base.metadata.create_all schema."""
        # DB 1: via migrations
        engine_m = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
        with engine_m.begin() as conn:
            command.upgrade(_test_cfg(conn), "head")

        # DB 2: via create_all
        engine_c = create_engine(f"sqlite:///{tmp_path / 'create_all.db'}")
        with engine_c.begin() as conn:
            Base.metadata.create_all(conn)

        # Compare table names
        with engine_m.begin() as conn:
            m_insp = inspect(conn)
            m_tables = set(m_insp.get_table_names()) - {"alembic_version"}
            m_cols = {t: {c["name"] for c in m_insp.get_columns(t)} for t in m_tables}

        with engine_c.begin() as conn:
            c_insp = inspect(conn)
            c_tables = set(c_insp.get_table_names())
            c_cols = {t: {c["name"] for c in c_insp.get_columns(t)} for t in c_tables}

        assert m_tables == c_tables, f"Table mismatch: {m_tables ^ c_tables}"

        for table in m_tables:
            assert m_cols[table] == c_cols[table], f"Column mismatch in '{table}'"

        engine_m.dispose()
        engine_c.dispose()


# ── ensure_db_migrated Tests (async, real temp DBs) ──────────────────────────


class TestEnsureDbMigrated:
    """Test the 3 scenarios in ensure_db_migrated()."""

    @pytest.mark.asyncio
    async def test_fresh_db_creates_tables_and_stamps(self, tmp_path):
        """Scenario 1: Empty DB -> create_all + stamp head."""
        db_path = tmp_path / "fresh.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        test_engine = create_async_engine(db_url)

        from app.config import settings

        original_url = settings.vault_db_url
        try:
            settings.vault_db_url = db_url
            with patch("app.core.migrations.engine", test_engine):
                await ensure_db_migrated()
        finally:
            settings.vault_db_url = original_url

        # Verify: tables + alembic_version stamped at 002
        sync_engine = create_engine(f"sqlite:///{db_path}")
        with sync_engine.begin() as conn:
            tables = set(inspect(conn).get_table_names())
            assert "api_keys" in tables
            assert "users" in tables
            assert "alembic_version" in tables
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert row[0] == "002"
        sync_engine.dispose()
        await test_engine.dispose()

    @pytest.mark.asyncio
    async def test_existing_db_without_alembic_gets_stamped(self, tmp_path):
        """Scenario 2: Tables exist but no alembic_version -> stamp head."""
        db_path = tmp_path / "existing.db"

        # Pre-create tables via create_all (no alembic_version)
        sync_engine = create_engine(f"sqlite:///{db_path}")
        with sync_engine.begin() as conn:
            Base.metadata.create_all(conn)
        sync_engine.dispose()

        db_url = f"sqlite+aiosqlite:///{db_path}"
        test_engine = create_async_engine(db_url)

        from app.config import settings

        original_url = settings.vault_db_url
        try:
            settings.vault_db_url = db_url
            with patch("app.core.migrations.engine", test_engine):
                await ensure_db_migrated()
        finally:
            settings.vault_db_url = original_url

        # Verify: alembic_version stamped at 002, tables unchanged
        sync_engine = create_engine(f"sqlite:///{db_path}")
        with sync_engine.begin() as conn:
            tables = set(inspect(conn).get_table_names())
            assert "alembic_version" in tables
            assert "api_keys" in tables
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert row[0] == "002"
        sync_engine.dispose()
        await test_engine.dispose()

    @pytest.mark.asyncio
    async def test_alembic_tracked_db_gets_upgraded(self, tmp_path):
        """Scenario 3: DB at revision 001 -> upgrade to 002."""
        db_path = tmp_path / "tracked.db"

        # Create DB at revision 001 via connection injection
        sync_engine = create_engine(f"sqlite:///{db_path}")
        with sync_engine.begin() as conn:
            command.upgrade(_test_cfg(conn), "001")
        # Verify no update_jobs table yet (added in 002)
        with sync_engine.begin() as conn:
            assert "update_jobs" not in set(inspect(conn).get_table_names())
        sync_engine.dispose()

        db_url = f"sqlite+aiosqlite:///{db_path}"
        test_engine = create_async_engine(db_url)

        from app.config import settings

        original_url = settings.vault_db_url
        try:
            settings.vault_db_url = db_url
            with patch("app.core.migrations.engine", test_engine):
                await ensure_db_migrated()
        finally:
            settings.vault_db_url = original_url

        # Verify: upgraded to 002 with update_jobs table
        sync_engine = create_engine(f"sqlite:///{db_path}")
        with sync_engine.begin() as conn:
            tables = set(inspect(conn).get_table_names())
            assert "update_jobs" in tables
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert row[0] == "002"
        sync_engine.dispose()
        await test_engine.dispose()
