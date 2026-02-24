import contextlib
import io
import json
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.core.database import (
    ApiKey,
    AuditLog,
    Conversation,
    Message,
    SystemConfig,
    TrainingJob,
)
from app.core.security import generate_api_key, hash_api_key, get_key_prefix


@contextlib.contextmanager
def _temp_backup_dir():
    """Create a temp directory and patch settings.vault_backup_dir to it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        original = settings.vault_backup_dir
        settings.vault_backup_dir = tmpdir
        try:
            yield tmpdir
        finally:
            settings.vault_backup_dir = original


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def auth_client(app_with_db, db_engine):
    """Authenticated admin client."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="diag-admin-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def user_client(app_with_db, db_engine):
    """Authenticated client with user scope (non-admin)."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="diag-user-test",
            scope="user",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def seed_data(db_engine):
    """Seed conversations, messages, training jobs, and audit entries."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    old_date = now - timedelta(days=90)

    async with session_factory() as session:
        # Old conversation
        conv_old = Conversation(
            id=str(uuid.uuid4()),
            title="Old chat",
            model_id="test-model",
            created_at=old_date,
            updated_at=old_date,
            archived=False,
        )
        session.add(conv_old)

        msg_old = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv_old.id,
            role="user",
            content="Old message",
            timestamp=old_date,
        )
        session.add(msg_old)

        # Recent conversation
        conv_new = Conversation(
            id=str(uuid.uuid4()),
            title="Recent chat",
            model_id="test-model",
            created_at=now,
            updated_at=now,
            archived=False,
        )
        session.add(conv_new)

        msg_new = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv_new.id,
            role="user",
            content="New message",
            timestamp=now,
        )
        session.add(msg_new)

        # Training job
        job = TrainingJob(
            id=str(uuid.uuid4()),
            name="Test Job",
            status="completed",
            model="test-model",
            dataset="test-data",
            created_at=now,
        )
        session.add(job)

        # Audit log entry
        audit = AuditLog(
            action="test_action",
            method="GET",
            path="/test",
            status_code=200,
            latency_ms=10.5,
        )
        session.add(audit)

        # System config
        session.add(SystemConfig(key="test.setting", value="test_value"))

        await session.commit()

    return {
        "conv_old_id": conv_old.id,
        "conv_new_id": conv_new.id,
        "msg_old_id": msg_old.id,
        "msg_new_id": msg_new.id,
        "job_id": job.id,
    }


# ── 11.5: Data Export ────────────────────────────────────────────────────────


class TestDataExport:
    async def test_export_returns_conversations_with_messages(self, auth_client, seed_data):
        resp = await auth_client.get("/vault/admin/data/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "conversations" in data
        assert len(data["conversations"]) >= 2
        # Check messages are included
        conv_with_msgs = [c for c in data["conversations"] if len(c["messages"]) > 0]
        assert len(conv_with_msgs) >= 2

    async def test_export_contains_api_keys_without_hashes(self, auth_client, seed_data):
        resp = await auth_client.get("/vault/admin/data/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "api_keys" in data
        assert len(data["api_keys"]) >= 1
        for key in data["api_keys"]:
            assert "key_hash" not in key
            assert "key_prefix" in key
            assert "label" in key

    async def test_export_contains_training_jobs(self, auth_client, seed_data):
        resp = await auth_client.get("/vault/admin/data/export")
        data = resp.json()
        assert "training_jobs" in data
        assert len(data["training_jobs"]) >= 1

    async def test_export_contains_system_config(self, auth_client, seed_data):
        resp = await auth_client.get("/vault/admin/data/export")
        data = resp.json()
        assert "system_config" in data
        assert "exported_at" in data

    async def test_export_has_exported_at_timestamp(self, auth_client, seed_data):
        resp = await auth_client.get("/vault/admin/data/export")
        data = resp.json()
        assert "exported_at" in data
        # Should be a valid ISO timestamp
        ts = data["exported_at"]
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        datetime.fromisoformat(ts)

    async def test_export_requires_admin(self, user_client, seed_data):
        resp = await user_client.get("/vault/admin/data/export")
        assert resp.status_code == 403

    async def test_export_empty_database(self, auth_client):
        """Export with no seed data returns empty lists."""
        resp = await auth_client.get("/vault/admin/data/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversations"] == []
        assert data["training_jobs"] == []


# ── 11.6: Data Purge ────────────────────────────────────────────────────────


class TestDataPurge:
    async def test_purge_deletes_conversations_and_messages(self, auth_client, seed_data):
        resp = await auth_client.post(
            "/vault/admin/data/purge",
            json={"confirmation": "DELETE ALL DATA"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "purged"
        assert data["deleted"]["conversations"] >= 2
        assert data["deleted"]["messages"] >= 2

    async def test_purge_deletes_training_jobs(self, auth_client, seed_data):
        resp = await auth_client.post(
            "/vault/admin/data/purge",
            json={"confirmation": "DELETE ALL DATA"},
        )
        data = resp.json()
        assert data["deleted"]["training_jobs"] >= 1

    async def test_purge_preserves_api_keys_by_default(self, auth_client, seed_data):
        resp = await auth_client.post(
            "/vault/admin/data/purge",
            json={"confirmation": "DELETE ALL DATA"},
        )
        data = resp.json()
        assert data["deleted"]["api_keys"] == 0

    async def test_purge_deletes_api_keys_when_requested(self, auth_client, seed_data):
        resp = await auth_client.post(
            "/vault/admin/data/purge",
            json={"confirmation": "DELETE ALL DATA", "include_api_keys": True},
        )
        data = resp.json()
        assert data["deleted"]["api_keys"] >= 1

    async def test_purge_wrong_confirmation_returns_400(self, auth_client, seed_data):
        resp = await auth_client.post(
            "/vault/admin/data/purge",
            json={"confirmation": "wrong"},
        )
        assert resp.status_code == 400

    async def test_purge_requires_admin(self, user_client, seed_data):
        resp = await user_client.post(
            "/vault/admin/data/purge",
            json={"confirmation": "DELETE ALL DATA"},
        )
        assert resp.status_code == 403

    async def test_purge_empty_database(self, auth_client):
        """Purge on empty DB returns zero counts."""
        resp = await auth_client.post(
            "/vault/admin/data/purge",
            json={"confirmation": "DELETE ALL DATA"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"]["conversations"] == 0
        assert data["deleted"]["messages"] == 0

    async def test_purge_idempotent(self, auth_client, seed_data):
        """Two purges in a row — second returns zeros."""
        await auth_client.post(
            "/vault/admin/data/purge",
            json={"confirmation": "DELETE ALL DATA"},
        )
        resp = await auth_client.post(
            "/vault/admin/data/purge",
            json={"confirmation": "DELETE ALL DATA"},
        )
        data = resp.json()
        assert data["deleted"]["conversations"] == 0


# ── 11.7: Chat Archive ──────────────────────────────────────────────────────


class TestChatArchive:
    async def test_archive_old_conversations(self, auth_client, seed_data):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        resp = await auth_client.post(
            "/vault/admin/conversations/archive",
            json={"before": cutoff},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["archived_count"] == 1  # Only the old one
        assert data["message_count"] == 1

    async def test_archive_preserves_recent(self, auth_client, seed_data):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        await auth_client.post(
            "/vault/admin/conversations/archive",
            json={"before": cutoff},
        )
        # List should only show the recent conversation
        resp = await auth_client.get("/vault/conversations")
        assert resp.status_code == 200
        convos = resp.json()
        assert len(convos) == 1
        assert convos[0]["title"] == "Recent chat"

    async def test_archived_excluded_from_list(self, auth_client, seed_data):
        # Archive everything
        cutoff = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await auth_client.post(
            "/vault/admin/conversations/archive",
            json={"before": cutoff},
        )
        resp = await auth_client.get("/vault/conversations")
        convos = resp.json()
        assert len(convos) == 0

    async def test_archived_still_accessible_by_id(self, auth_client, seed_data):
        cutoff = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await auth_client.post(
            "/vault/admin/conversations/archive",
            json={"before": cutoff},
        )
        # Direct access by ID should still work
        resp = await auth_client.get(f"/vault/conversations/{seed_data['conv_old_id']}")
        assert resp.status_code == 200

    async def test_archive_idempotent(self, auth_client, seed_data):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        await auth_client.post(
            "/vault/admin/conversations/archive",
            json={"before": cutoff},
        )
        # Second archive — nothing new to archive
        resp = await auth_client.post(
            "/vault/admin/conversations/archive",
            json={"before": cutoff},
        )
        data = resp.json()
        assert data["archived_count"] == 0

    async def test_archive_requires_admin(self, user_client, seed_data):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        resp = await user_client.post(
            "/vault/admin/conversations/archive",
            json={"before": cutoff},
        )
        assert resp.status_code == 403

    async def test_archive_no_matching_conversations(self, auth_client, seed_data):
        # Use a date far in the past — nothing older
        cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        resp = await auth_client.post(
            "/vault/admin/conversations/archive",
            json={"before": cutoff},
        )
        data = resp.json()
        assert data["archived_count"] == 0
        assert data["message_count"] == 0

    async def test_archive_empty_database(self, auth_client):
        cutoff = datetime.now(timezone.utc).isoformat()
        resp = await auth_client.post(
            "/vault/admin/conversations/archive",
            json={"before": cutoff},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["archived_count"] == 0


# ── 11.4: Factory Reset ─────────────────────────────────────────────────────


class TestFactoryReset:
    async def test_factory_reset_clears_user_tables(self, auth_client, seed_data):
        resp = await auth_client.post(
            "/vault/admin/factory-reset",
            json={"confirmation": "FACTORY RESET"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "reset"
        assert "conversations" in data["cleared"]
        assert "messages" in data["cleared"]
        assert "training_jobs" in data["cleared"]
        assert "audit_log" in data["cleared"]

    async def test_factory_reset_resets_config(self, auth_client, seed_data):
        resp = await auth_client.post(
            "/vault/admin/factory-reset",
            json={"confirmation": "FACTORY RESET"},
        )
        data = resp.json()
        assert "system_config" in data["cleared"]

    async def test_factory_reset_wrong_confirmation(self, auth_client, seed_data):
        resp = await auth_client.post(
            "/vault/admin/factory-reset",
            json={"confirmation": "wrong"},
        )
        assert resp.status_code == 400

    async def test_factory_reset_requires_admin(self, user_client, seed_data):
        resp = await user_client.post(
            "/vault/admin/factory-reset",
            json={"confirmation": "FACTORY RESET"},
        )
        assert resp.status_code == 403

    async def test_factory_reset_sets_setup_incomplete(self, auth_client, app_with_db, seed_data):
        app_with_db.state.setup_complete = True
        await auth_client.post(
            "/vault/admin/factory-reset",
            json={"confirmation": "FACTORY RESET"},
        )
        assert app_with_db.state.setup_complete is False

    async def test_factory_reset_conversations_empty_after(self, auth_client, seed_data):
        await auth_client.post(
            "/vault/admin/factory-reset",
            json={"confirmation": "FACTORY RESET"},
        )
        resp = await auth_client.get("/vault/conversations")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_factory_reset_message(self, auth_client, seed_data):
        resp = await auth_client.post(
            "/vault/admin/factory-reset",
            json={"confirmation": "FACTORY RESET"},
        )
        data = resp.json()
        assert "Setup wizard" in data["message"]


# ── 11.1: Support Bundle ────────────────────────────────────────────────────


class TestSupportBundle:
    async def test_bundle_returns_valid_tarball(self, auth_client, seed_data):
        resp = await auth_client.post("/vault/admin/diagnostics/bundle")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/gzip"
        # Validate it's a valid gzip tarball
        buf = io.BytesIO(resp.content)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            names = tar.getnames()
            assert "system_info.json" in names

    async def test_bundle_contains_expected_files(self, auth_client, seed_data):
        resp = await auth_client.post("/vault/admin/diagnostics/bundle")
        buf = io.BytesIO(resp.content)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            names = tar.getnames()
            assert "system_info.json" in names
            assert "config.json" in names
            assert "audit_log.json" in names

    async def test_bundle_system_info_has_required_fields(self, auth_client, seed_data):
        resp = await auth_client.post("/vault/admin/diagnostics/bundle")
        buf = io.BytesIO(resp.content)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            member = tar.getmember("system_info.json")
            f = tar.extractfile(member)
            info = json.loads(f.read())
            assert "platform" in info
            assert "python_version" in info
            assert "timestamp" in info

    async def test_bundle_redacts_secrets(self, auth_client, db_engine, seed_data):
        """Config in bundle should not contain raw secrets."""
        # Add a secret-looking config entry
        sf = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as session:
            session.add(SystemConfig(key="vault_secret_key_test", value="super-secret"))
            await session.commit()

        resp = await auth_client.post("/vault/admin/diagnostics/bundle")
        buf = io.BytesIO(resp.content)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            f = tar.extractfile(tar.getmember("config.json"))
            config = json.loads(f.read())
            # No config value should contain the raw secret
            for key, val in config.items():
                if "secret" in key.lower():
                    assert val == "***REDACTED***"

    async def test_bundle_requires_admin(self, user_client, seed_data):
        resp = await user_client.post("/vault/admin/diagnostics/bundle")
        assert resp.status_code == 403

    async def test_bundle_audit_log_included(self, auth_client, seed_data):
        resp = await auth_client.post("/vault/admin/diagnostics/bundle")
        buf = io.BytesIO(resp.content)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            f = tar.extractfile(tar.getmember("audit_log.json"))
            audit = json.loads(f.read())
            assert isinstance(audit, list)
            assert len(audit) >= 1


# ── 11.2: Backup ────────────────────────────────────────────────────────────


class TestBackup:
    async def test_backup_creates_file(self, auth_client, seed_data):
        with _temp_backup_dir() as tmpdir:
            resp = await auth_client.post(
                "/vault/admin/backup",
                json={"output_path": tmpdir},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert Path(data["path"]).exists()

    async def test_backup_checksum_present(self, auth_client, seed_data):
        with _temp_backup_dir() as tmpdir:
            resp = await auth_client.post(
                "/vault/admin/backup",
                json={"output_path": tmpdir},
            )
            data = resp.json()
            assert len(data["checksum_sha256"]) == 64

    async def test_backup_unencrypted(self, auth_client, seed_data):
        with _temp_backup_dir() as tmpdir:
            resp = await auth_client.post(
                "/vault/admin/backup",
                json={"output_path": tmpdir},
            )
            data = resp.json()
            assert data["encrypted"] is False
            assert data["filename"].endswith(".tar.gz")
            # Verify it's a valid tarball
            with tarfile.open(data["path"], "r:gz") as tar:
                assert "vault.db" in tar.getnames()

    async def test_backup_encrypted(self, auth_client, seed_data):
        with _temp_backup_dir() as tmpdir:
            resp = await auth_client.post(
                "/vault/admin/backup",
                json={"output_path": tmpdir, "passphrase": "test-secret-123"},
            )
            data = resp.json()
            assert data["encrypted"] is True
            assert data["filename"].endswith(".enc")

    async def test_backup_contains_db_and_config(self, auth_client, seed_data):
        with _temp_backup_dir() as tmpdir:
            resp = await auth_client.post(
                "/vault/admin/backup",
                json={"output_path": tmpdir},
            )
            data = resp.json()
            with tarfile.open(data["path"], "r:gz") as tar:
                names = tar.getnames()
                assert "vault.db" in names

    async def test_backup_path_traversal_blocked(self, auth_client, seed_data):
        """Attempting to write backups outside the backup dir returns 403."""
        resp = await auth_client.post(
            "/vault/admin/backup",
            json={"output_path": "/tmp/evil-escape"},
        )
        assert resp.status_code == 403

    async def test_backup_requires_admin(self, user_client, seed_data):
        resp = await user_client.post(
            "/vault/admin/backup",
            json={},
        )
        assert resp.status_code == 403

    async def test_backup_size_bytes_positive(self, auth_client, seed_data):
        with _temp_backup_dir() as tmpdir:
            resp = await auth_client.post(
                "/vault/admin/backup",
                json={"output_path": tmpdir},
            )
            data = resp.json()
            assert data["size_bytes"] > 0


# ── 11.3: Restore ───────────────────────────────────────────────────────────


class TestRestore:
    async def test_roundtrip_backup_restore(self, auth_client, seed_data):
        """Backup then restore succeeds."""
        with _temp_backup_dir() as tmpdir:
            # Backup
            backup_resp = await auth_client.post(
                "/vault/admin/backup",
                json={"output_path": tmpdir},
            )
            backup_path = backup_resp.json()["path"]

            # Restore
            resp = await auth_client.post(
                "/vault/admin/restore",
                json={"backup_path": backup_path},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "vault.db" in data["tables_restored"]

    async def test_encrypted_roundtrip(self, auth_client, seed_data):
        """Backup with passphrase, restore with same passphrase."""
        with _temp_backup_dir() as tmpdir:
            backup_resp = await auth_client.post(
                "/vault/admin/backup",
                json={"output_path": tmpdir, "passphrase": "my-secret"},
            )
            backup_path = backup_resp.json()["path"]

            resp = await auth_client.post(
                "/vault/admin/restore",
                json={"backup_path": backup_path, "passphrase": "my-secret"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    async def test_wrong_passphrase_fails(self, auth_client, seed_data):
        """Encrypted backup with wrong passphrase returns error."""
        with _temp_backup_dir() as tmpdir:
            backup_resp = await auth_client.post(
                "/vault/admin/backup",
                json={"output_path": tmpdir, "passphrase": "correct-pass"},
            )
            backup_path = backup_resp.json()["path"]

            resp = await auth_client.post(
                "/vault/admin/restore",
                json={"backup_path": backup_path, "passphrase": "wrong-pass"},
            )
            assert resp.status_code == 400

    async def test_restore_path_traversal_blocked(self, auth_client):
        """Attempting to restore from outside the backup dir returns 403."""
        resp = await auth_client.post(
            "/vault/admin/restore",
            json={"backup_path": "/etc/passwd"},
        )
        assert resp.status_code == 403

    async def test_restore_file_not_found(self, auth_client):
        with _temp_backup_dir():
            resp = await auth_client.post(
                "/vault/admin/restore",
                json={"backup_path": settings.vault_backup_dir + "/nonexistent.tar.gz"},
            )
            assert resp.status_code == 400

    async def test_restore_invalid_archive(self, auth_client):
        """Restore a file that isn't a valid tarball."""
        with _temp_backup_dir() as tmpdir:
            bad_file = Path(tmpdir) / "bad.tar.gz"
            bad_file.write_bytes(b"not a tarball")
            resp = await auth_client.post(
                "/vault/admin/restore",
                json={"backup_path": str(bad_file)},
            )
            assert resp.status_code == 400

    async def test_restore_requires_admin(self, user_client, seed_data):
        resp = await user_client.post(
            "/vault/admin/restore",
            json={"backup_path": "/some/path.tar.gz"},
        )
        assert resp.status_code == 403

    async def test_restore_missing_vault_db(self, auth_client):
        """Tarball without vault.db should fail."""
        with _temp_backup_dir() as tmpdir:
            bad_tar_path = Path(tmpdir) / "no-db.tar.gz"
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                content = b"dummy"
                info = tarfile.TarInfo(name="dummy.txt")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            bad_tar_path.write_bytes(buf.getvalue())

            resp = await auth_client.post(
                "/vault/admin/restore",
                json={"backup_path": str(bad_tar_path)},
            )
            assert resp.status_code == 400
            assert "vault.db" in resp.json()["error"]["message"]

    async def test_restore_zip_slip_blocked(self, auth_client):
        """Tarball with path traversal entries is rejected."""
        with _temp_backup_dir() as tmpdir:
            evil_tar_path = Path(tmpdir) / "evil.tar.gz"
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                # Add vault.db so it passes the member check
                db_content = b"fake-db"
                db_info = tarfile.TarInfo(name="vault.db")
                db_info.size = len(db_content)
                tar.addfile(db_info, io.BytesIO(db_content))
                # Add path-traversal entry
                evil_content = b"pwned"
                evil_info = tarfile.TarInfo(name="../../etc/crontab")
                evil_info.size = len(evil_content)
                tar.addfile(evil_info, io.BytesIO(evil_content))
            evil_tar_path.write_bytes(buf.getvalue())

            resp = await auth_client.post(
                "/vault/admin/restore",
                json={"backup_path": str(evil_tar_path)},
            )
            assert resp.status_code == 400
            assert "traversal" in resp.json()["error"]["message"].lower()

    async def test_restore_message_includes_count(self, auth_client, seed_data):
        with _temp_backup_dir() as tmpdir:
            backup_resp = await auth_client.post(
                "/vault/admin/backup",
                json={"output_path": tmpdir},
            )
            backup_path = backup_resp.json()["path"]

            resp = await auth_client.post(
                "/vault/admin/restore",
                json={"backup_path": backup_path},
            )
            data = resp.json()
            assert "Restored" in data["message"]
            assert len(data["tables_restored"]) >= 1
