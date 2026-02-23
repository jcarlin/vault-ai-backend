import asyncio
import hashlib
import io
import json
import os
import platform
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import structlog
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import base64
from sqlalchemy import delete, func, select, update

import app.core.database as db_module
from app.config import settings
from app.core.database import (
    ApiKey,
    AuditLog,
    Conversation,
    Message,
    SystemConfig,
    TrainingJob,
)
from app.core.exceptions import VaultError
from app.services.admin import (
    MODEL_DEFAULTS,
    NETWORK_DEFAULTS,
    QUARANTINE_DEFAULTS,
    SYSTEM_DEFAULTS,
)

logger = structlog.get_logger()

# Sensitive keys to redact from support bundles
_REDACT_KEYS = {"key_hash", "vault_secret_key", "vault_access_key", "vault_admin_api_key"}


def _derive_fernet_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a Fernet key from a passphrase using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


class DiagnosticsService:
    def __init__(self, session_factory=None):
        self._session_factory = session_factory or db_module.async_session

    # ── 11.5: Data Export ────────────────────────────────────────────────────

    async def export_data(self) -> dict:
        """Export all user data as a JSON-serializable dict."""
        async with self._session_factory() as session:
            # Conversations + messages
            conv_result = await session.execute(
                select(Conversation).order_by(Conversation.created_at.desc())
            )
            conversations = list(conv_result.scalars().all())

            exported_convos = []
            for conv in conversations:
                msg_result = await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.timestamp.asc())
                )
                messages = list(msg_result.scalars().all())
                exported_convos.append({
                    "id": conv.id,
                    "title": conv.title,
                    "model_id": conv.model_id,
                    "created_at": conv.created_at.isoformat() + "Z",
                    "updated_at": conv.updated_at.isoformat() + "Z",
                    "messages": [
                        {
                            "id": m.id,
                            "role": m.role,
                            "content": m.content,
                            "timestamp": m.timestamp.isoformat() + "Z",
                        }
                        for m in messages
                    ],
                })

            # API keys (metadata only — no key_hash)
            key_result = await session.execute(
                select(ApiKey).order_by(ApiKey.created_at.desc())
            )
            keys = list(key_result.scalars().all())
            exported_keys = [
                {
                    "id": k.id,
                    "key_prefix": k.key_prefix,
                    "label": k.label,
                    "scope": k.scope,
                    "is_active": k.is_active,
                    "created_at": k.created_at.isoformat() + "Z",
                    "last_used_at": k.last_used_at.isoformat() + "Z" if k.last_used_at else None,
                }
                for k in keys
            ]

            # Training jobs
            job_result = await session.execute(
                select(TrainingJob).order_by(TrainingJob.created_at.desc())
            )
            jobs = list(job_result.scalars().all())
            exported_jobs = [
                {
                    "id": j.id,
                    "name": j.name,
                    "status": j.status,
                    "model": j.model,
                    "dataset": j.dataset,
                    "created_at": j.created_at.isoformat() + "Z",
                }
                for j in jobs
            ]

            # System config
            config_result = await session.execute(
                select(SystemConfig).order_by(SystemConfig.key)
            )
            configs = list(config_result.scalars().all())
            exported_config = [
                {"key": c.key, "value": c.value}
                for c in configs
                if not any(s in c.key.lower() for s in ("secret", "key_hash"))
            ]

        return {
            "conversations": exported_convos,
            "api_keys": exported_keys,
            "training_jobs": exported_jobs,
            "system_config": exported_config,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }

    # ── 11.6: Data Purge ────────────────────────────────────────────────────

    async def purge_data(self, include_api_keys: bool = False) -> dict:
        """Delete all user data. Returns counts of deleted rows."""
        async with self._session_factory() as session:
            # Count before deleting
            msg_count = (await session.execute(select(func.count()).select_from(Message))).scalar() or 0
            conv_count = (await session.execute(select(func.count()).select_from(Conversation))).scalar() or 0
            job_count = (await session.execute(select(func.count()).select_from(TrainingJob))).scalar() or 0

            # Delete in order (messages first due to FK)
            await session.execute(delete(Message))
            await session.execute(delete(Conversation))
            await session.execute(delete(TrainingJob))

            key_count = 0
            if include_api_keys:
                key_count = (await session.execute(select(func.count()).select_from(ApiKey))).scalar() or 0
                await session.execute(delete(ApiKey))

            await session.commit()

        return {
            "status": "purged",
            "deleted": {
                "conversations": conv_count,
                "messages": msg_count,
                "training_jobs": job_count,
                "api_keys": key_count,
            },
        }

    # ── 11.7: Chat Archive ──────────────────────────────────────────────────

    async def archive_conversations(self, before: datetime) -> dict:
        """Mark conversations older than `before` as archived."""
        async with self._session_factory() as session:
            # Count conversations to archive
            count_stmt = (
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.updated_at < before, Conversation.archived == False)  # noqa: E712
            )
            conv_count = (await session.execute(count_stmt)).scalar() or 0

            if conv_count == 0:
                return {"status": "ok", "archived_count": 0, "message_count": 0}

            # Count messages in those conversations
            msg_count_stmt = (
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id.in_(
                        select(Conversation.id).where(
                            Conversation.updated_at < before,
                            Conversation.archived == False,  # noqa: E712
                        )
                    )
                )
            )
            msg_count = (await session.execute(msg_count_stmt)).scalar() or 0

            # Archive
            await session.execute(
                update(Conversation)
                .where(Conversation.updated_at < before, Conversation.archived == False)  # noqa: E712
                .values(archived=True)
            )
            await session.commit()

        return {
            "status": "ok",
            "archived_count": conv_count,
            "message_count": msg_count,
        }

    # ── 11.4: Factory Reset ─────────────────────────────────────────────────

    async def factory_reset(self, app_state) -> dict:
        """Reset system to factory defaults. Preserves API keys."""
        cleared = []

        async with self._session_factory() as session:
            # Delete user data tables
            await session.execute(delete(Message))
            await session.execute(delete(Conversation))
            cleared.extend(["conversations", "messages"])

            await session.execute(delete(TrainingJob))
            cleared.append("training_jobs")

            await session.execute(delete(AuditLog))
            cleared.append("audit_log")

            # Reset system config to defaults
            await session.execute(delete(SystemConfig))
            all_defaults = {
                **NETWORK_DEFAULTS,
                **SYSTEM_DEFAULTS,
                **MODEL_DEFAULTS,
                **QUARANTINE_DEFAULTS,
            }
            for key, value in all_defaults.items():
                session.add(SystemConfig(key=key, value=value))

            await session.commit()
            cleared.append("system_config")

        # Delete setup flag file to re-trigger wizard
        try:
            flag_path = Path(settings.vault_setup_flag_path)
            if flag_path.exists():
                flag_path.unlink()
                logger.info("factory_reset_flag_deleted", path=str(flag_path))
        except Exception as e:
            logger.warning("factory_reset_flag_delete_failed", error=str(e))

        # Reset in-memory setup state
        app_state.setup_complete = False

        return {
            "status": "reset",
            "message": "Factory reset complete. Setup wizard will re-appear on next access.",
            "cleared": cleared,
        }

    # ── 11.1: Support Bundle ────────────────────────────────────────────────

    async def generate_bundle(self) -> bytes:
        """Generate a support bundle tarball (in-memory)."""
        buf = io.BytesIO()

        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            # 1. System info
            sys_info = {
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "architecture": platform.machine(),
                "hostname": platform.node(),
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            }

            # Disk/RAM
            try:
                import psutil
                disk = psutil.disk_usage("/")
                mem = psutil.virtual_memory()
                sys_info["disk_total_gb"] = round(disk.total / (1024**3), 1)
                sys_info["disk_used_gb"] = round(disk.used / (1024**3), 1)
                sys_info["ram_total_gb"] = round(mem.total / (1024**3), 1)
                sys_info["ram_used_gb"] = round(mem.used / (1024**3), 1)
            except Exception:
                pass

            # GPU info
            try:
                import py3nvml.py3nvml as nvml
                nvml.nvmlInit()
                gpu_count = nvml.nvmlDeviceGetCount()
                gpus = []
                for i in range(gpu_count):
                    handle = nvml.nvmlDeviceGetHandleByIndex(i)
                    gpus.append({
                        "index": i,
                        "name": nvml.nvmlDeviceGetName(handle),
                        "memory_total_mb": nvml.nvmlDeviceGetMemoryInfo(handle).total // (1024 * 1024),
                    })
                sys_info["gpus"] = gpus
                nvml.nvmlShutdown()
            except Exception:
                sys_info["gpus"] = []

            self._add_json_to_tar(tar, "system_info.json", sys_info)

            # 2. System config (redacted)
            async with self._session_factory() as session:
                config_result = await session.execute(select(SystemConfig).order_by(SystemConfig.key))
                configs = list(config_result.scalars().all())

            redacted_config = {}
            for c in configs:
                if any(s in c.key.lower() for s in ("secret", "key_hash", "password")):
                    redacted_config[c.key] = "***REDACTED***"
                else:
                    redacted_config[c.key] = c.value

            self._add_json_to_tar(tar, "config.json", redacted_config)

            # 3. Recent audit log (last 500)
            async with self._session_factory() as session:
                audit_result = await session.execute(
                    select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(500)
                )
                audit_entries = list(audit_result.scalars().all())

            audit_data = [
                {
                    "id": e.id,
                    "timestamp": e.timestamp.isoformat() + "Z" if e.timestamp else None,
                    "action": e.action,
                    "method": e.method,
                    "path": e.path,
                    "status_code": e.status_code,
                    "latency_ms": e.latency_ms,
                }
                for e in audit_entries
            ]
            self._add_json_to_tar(tar, "audit_log.json", audit_data)

            # 4. Model manifest
            manifest_path = Path(settings.vault_models_manifest)
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text())
                    self._add_json_to_tar(tar, "models.json", manifest)
                except Exception:
                    pass

        return buf.getvalue()

    @staticmethod
    def _add_json_to_tar(tar: tarfile.TarFile, name: str, data) -> None:
        content = json.dumps(data, indent=2, default=str).encode()
        info = tarfile.TarInfo(name=name)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))

    # ── 11.2: Backup ────────────────────────────────────────────────────────

    async def create_backup(self, output_path: str | None = None, passphrase: str | None = None) -> dict:
        """Create a backup of the database and config files."""
        output_dir = Path(output_path) if output_path else Path(settings.vault_backup_dir)

        if not output_dir.exists():
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise VaultError(
                    code="backup_error",
                    message=f"Cannot create backup directory: {e}",
                    status=400,
                )

        if not output_dir.is_dir():
            raise VaultError(
                code="backup_error",
                message=f"Output path is not a directory: {output_dir}",
                status=400,
            )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"vault-backup-{timestamp}.tar.gz"
        if passphrase:
            filename += ".enc"

        final_path = output_dir / filename

        # Create tarball in a temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            db_url = settings.vault_db_url

            # 1. Database backup
            if db_url.startswith("postgresql"):
                # PostgreSQL: pg_dump to custom format (compressed, supports pg_restore)
                db_backup_path = tmpdir_path / "vault.pgdump"
                db_arcname = "vault.pgdump"
                await self._pg_dump(db_url, db_backup_path)
            else:
                # SQLite backup using .backup() API
                db_backup_path = tmpdir_path / "vault.db"
                db_arcname = "vault.db"
                if ":///" in db_url:
                    source_db_path = db_url.split(":///", 1)[1]
                else:
                    source_db_path = "data/vault.db"

                source_path = Path(source_db_path)
                if source_path.exists():
                    src_conn = sqlite3.connect(str(source_path))
                    dst_conn = sqlite3.connect(str(db_backup_path))
                    src_conn.backup(dst_conn)
                    src_conn.close()
                    dst_conn.close()
                else:
                    async with self._session_factory() as session:
                        conn = await session.connection()
                        raw_conn = await conn.get_raw_connection()
                        raw_sqlite = raw_conn.dbapi_connection
                        dst_conn = sqlite3.connect(str(db_backup_path))
                        raw_sqlite.backup(dst_conn)
                        dst_conn.close()

            # 2. Config files
            config_dir = tmpdir_path / "config"
            config_dir.mkdir()

            manifest_path = Path(settings.vault_models_manifest)
            if manifest_path.exists():
                shutil.copy2(manifest_path, config_dir / manifest_path.name)

            gpu_config_path = Path(settings.vault_gpu_config_path)
            if gpu_config_path.exists():
                shutil.copy2(gpu_config_path, config_dir / gpu_config_path.name)

            # 3. TLS certs
            tls_dir = Path(settings.vault_tls_cert_dir)
            if tls_dir.exists() and tls_dir.is_dir():
                tls_backup_dir = tmpdir_path / "tls"
                tls_backup_dir.mkdir()
                for f in tls_dir.iterdir():
                    if f.is_file():
                        shutil.copy2(f, tls_backup_dir / f.name)

            # Create tarball
            tar_path = tmpdir_path / "backup.tar.gz"
            with tarfile.open(tar_path, "w:gz") as tar:
                if db_backup_path.exists():
                    tar.add(db_backup_path, arcname=db_arcname)
                if config_dir.exists():
                    for f in config_dir.iterdir():
                        tar.add(f, arcname=f"config/{f.name}")
                tls_backup = tmpdir_path / "tls"
                if tls_backup.exists():
                    for f in tls_backup.iterdir():
                        tar.add(f, arcname=f"tls/{f.name}")

            tar_bytes = tar_path.read_bytes()

            # Optionally encrypt
            if passphrase:
                salt = os.urandom(16)
                key = _derive_fernet_key(passphrase, salt)
                f = Fernet(key)
                encrypted = f.encrypt(tar_bytes)
                # Prepend salt for decryption
                final_bytes = salt + encrypted
            else:
                final_bytes = tar_bytes

            final_path.write_bytes(final_bytes)

        # Compute checksum
        checksum = hashlib.sha256(final_path.read_bytes()).hexdigest()
        size_bytes = final_path.stat().st_size

        logger.info("backup_created", path=str(final_path), size=size_bytes, encrypted=bool(passphrase))

        return {
            "status": "ok",
            "filename": filename,
            "path": str(final_path),
            "size_bytes": size_bytes,
            "encrypted": bool(passphrase),
            "checksum_sha256": checksum,
        }

    # ── 11.3: Restore ───────────────────────────────────────────────────────

    async def restore_backup(self, backup_path: str, passphrase: str | None = None) -> dict:
        """Restore from a backup tarball."""
        path = Path(backup_path)
        if not path.exists():
            raise VaultError(
                code="restore_error",
                message=f"Backup file not found: {backup_path}",
                status=400,
            )

        raw_bytes = path.read_bytes()

        # Decrypt if needed
        if passphrase:
            try:
                salt = raw_bytes[:16]
                encrypted = raw_bytes[16:]
                key = _derive_fernet_key(passphrase, salt)
                f = Fernet(key)
                raw_bytes = f.decrypt(encrypted)
            except Exception as e:
                raise VaultError(
                    code="restore_error",
                    message=f"Decryption failed — wrong passphrase or corrupted file: {e}",
                    status=400,
                )

        # Validate tarball structure
        try:
            buf = io.BytesIO(raw_bytes)
            tar = tarfile.open(fileobj=buf, mode="r:gz")
            members = tar.getnames()
            tar.close()
        except Exception as e:
            raise VaultError(
                code="restore_error",
                message=f"Invalid backup archive: {e}",
                status=400,
            )

        # Determine DB type and validate archive contents
        db_url = settings.vault_db_url
        is_pg = db_url.startswith("postgresql")
        has_pgdump = "vault.pgdump" in members
        has_sqlite = "vault.db" in members

        if is_pg and not has_pgdump and not has_sqlite:
            raise VaultError(
                code="restore_error",
                message="Backup archive does not contain vault.pgdump or vault.db",
                status=400,
            )
        if not is_pg and not has_sqlite:
            raise VaultError(
                code="restore_error",
                message="Backup archive does not contain vault.db",
                status=400,
            )

        tables_restored = []

        # Extract and restore
        with tempfile.TemporaryDirectory() as tmpdir:
            buf = io.BytesIO(raw_bytes)
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                tar.extractall(path=tmpdir)

            tmpdir_path = Path(tmpdir)

            # Restore database
            if is_pg:
                backup_pgdump = tmpdir_path / "vault.pgdump"
                if backup_pgdump.exists():
                    await self._pg_restore(db_url, backup_pgdump)
                    tables_restored.append("vault.pgdump")
                elif has_sqlite:
                    logger.warning("restore_sqlite_backup_on_pg",
                                   message="SQLite backup detected on PostgreSQL system. "
                                           "Use migrate_sqlite_to_pg.py to import.")
                    tables_restored.append("vault.db (skipped — use migration script)")
            else:
                # SQLite restore
                if ":///" in db_url:
                    target_db_path = Path(db_url.split(":///", 1)[1])
                else:
                    target_db_path = Path("data/vault.db")

                backup_db = tmpdir_path / "vault.db"

                if target_db_path.exists():
                    bak_path = target_db_path.with_suffix(".db.bak")
                    shutil.copy2(target_db_path, bak_path)
                    logger.info("restore_old_db_backed_up", path=str(bak_path))

                if backup_db.exists() and target_db_path.parent.exists():
                    shutil.copy2(backup_db, target_db_path)
                    tables_restored.append("vault.db")

            # Restore config files
            config_src = tmpdir_path / "config"
            if config_src.exists():
                manifest_path = Path(settings.vault_models_manifest)
                src_manifest = config_src / manifest_path.name
                if src_manifest.exists():
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_manifest, manifest_path)
                    tables_restored.append(f"config/{manifest_path.name}")

                gpu_path = Path(settings.vault_gpu_config_path)
                src_gpu = config_src / gpu_path.name
                if src_gpu.exists():
                    gpu_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_gpu, gpu_path)
                    tables_restored.append(f"config/{gpu_path.name}")

            # Restore TLS certs
            tls_src = tmpdir_path / "tls"
            if tls_src.exists():
                tls_dir = Path(settings.vault_tls_cert_dir)
                tls_dir.mkdir(parents=True, exist_ok=True)
                for f in tls_src.iterdir():
                    if f.is_file():
                        shutil.copy2(f, tls_dir / f.name)
                        tables_restored.append(f"tls/{f.name}")

        logger.info("restore_completed", restored=tables_restored)

        return {
            "status": "ok",
            "tables_restored": tables_restored,
            "message": f"Restored {len(tables_restored)} items from backup. Restart the service to apply database changes.",
        }

    # ── PostgreSQL helpers ───────────────────────────────────────────────

    @staticmethod
    def _parse_pg_url(db_url: str) -> tuple[str, str, str, str]:
        """Parse a PostgreSQL URL into (host, port, user, dbname) and set PGPASSWORD."""
        parsed = urlparse(db_url.replace("+asyncpg", ""))
        host = parsed.hostname or "localhost"
        port = str(parsed.port or 5432)
        user = parsed.username or "vault"
        dbname = parsed.path.lstrip("/")
        password = parsed.password or ""
        return host, port, user, dbname, password

    async def _pg_dump(self, db_url: str, output_path: Path) -> None:
        """Run pg_dump to create a custom-format backup."""
        host, port, user, dbname, password = self._parse_pg_url(db_url)
        env = {**os.environ, "PGPASSWORD": password}
        cmd = [
            "pg_dump", "-Fc",
            "-h", host, "-p", port, "-U", user,
            "-d", dbname, "-f", str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise VaultError(
                code="backup_error",
                message=f"pg_dump failed: {stderr.decode().strip()}",
                status=500,
            )

    async def _pg_restore(self, db_url: str, backup_path: Path) -> None:
        """Run pg_restore to restore from a custom-format backup."""
        host, port, user, dbname, password = self._parse_pg_url(db_url)
        env = {**os.environ, "PGPASSWORD": password}
        cmd = [
            "pg_restore", "--clean", "--if-exists",
            "-h", host, "-p", port, "-U", user,
            "-d", dbname, str(backup_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise VaultError(
                code="restore_error",
                message=f"pg_restore failed: {stderr.decode().strip()}",
                status=500,
            )
