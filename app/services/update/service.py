"""Core update service: scan, verify, apply, rollback, status, history."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import semver
import structlog
from sqlalchemy import func, select

import app.core.database as db_module
from app.config import settings
from app.core.database import AuditLog, SystemConfig, UpdateJob
from app.core.exceptions import NotFoundError, VaultError
from app.services.update.bundle import UpdateBundle
from app.services.update.directory import UpdateDirectory
from app.services.update.engine import UpdateEngine
from app.services.update.gpg import GPGVerifier
from app.services.update.scanner import USBScanner

logger = structlog.get_logger()

UPDATE_DEFAULTS = {
    "update.current_version": "1.0.0",
    "update.last_update_at": "",
    "update.auto_health_check": "true",
    "update.auto_rollback": "true",
    "update.health_check_timeout_seconds": "120",
}


class UpdateService:
    """Orchestrates the update lifecycle: scan, verify, apply, rollback."""

    def __init__(
        self,
        directory: UpdateDirectory,
        gpg_verifier: GPGVerifier | None = None,
        scanner: USBScanner | None = None,
        session_factory=None,
    ):
        self._directory = directory
        self._gpg = gpg_verifier or GPGVerifier()
        self._scanner = scanner or USBScanner()
        self._session_factory = session_factory or db_module.async_session
        self._engine = UpdateEngine(
            directory=directory,
            gpg_verifier=self._gpg,
            session_factory=self._session_factory,
        )
        # Cache of last scan results
        self._pending_bundles: list[dict] | None = None

    # ── Configuration ────────────────────────────────────────────────────────

    async def _populate_defaults(self) -> None:
        """Ensure all update config keys exist in SystemConfig."""
        async with self._session_factory() as session:
            for key, value in UPDATE_DEFAULTS.items():
                existing = await session.execute(
                    select(SystemConfig).where(SystemConfig.key == key)
                )
                if existing.scalar_one_or_none() is None:
                    session.add(SystemConfig(key=key, value=value))
            await session.commit()

    async def _get_config_value(self, key: str) -> str:
        """Read a single config value, populating defaults if needed."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == key)
            )
            row = result.scalar_one_or_none()
            if row:
                return row.value

        # Key missing — populate all defaults and return
        await self._populate_defaults()
        return UPDATE_DEFAULTS.get(key, "")

    # ── Status ───────────────────────────────────────────────────────────────

    async def get_status(self) -> dict:
        """Current system version, last update info, rollback availability."""
        current_version = await self._get_config_value("update.current_version")
        last_update_at = await self._get_config_value("update.last_update_at")

        # Get last successful update job
        async with self._session_factory() as session:
            result = await session.execute(
                select(UpdateJob)
                .where(UpdateJob.status == "completed")
                .order_by(UpdateJob.completed_at.desc())
                .limit(1)
            )
            last_job = result.scalar_one_or_none()

            # Count total updates
            count_result = await session.execute(
                select(func.count())
                .select_from(UpdateJob)
                .where(UpdateJob.status == "completed")
            )
            update_count = count_result.scalar() or 0

        rollback_available = self._directory.has_rollback()
        rollback_version = None
        if rollback_available:
            version_file = self._directory.rollback / "version.json"
            if version_file.exists():
                try:
                    data = json.loads(version_file.read_text())
                    rollback_version = data.get("version")
                except (json.JSONDecodeError, OSError):
                    pass

        return {
            "current_version": current_version,
            "last_update_at": last_update_at or None,
            "last_update_version": last_job.bundle_version if last_job else None,
            "rollback_available": rollback_available,
            "rollback_version": rollback_version,
            "update_count": update_count,
        }

    # ── Scan ─────────────────────────────────────────────────────────────────

    async def scan_for_updates(self) -> dict:
        """Scan USB/external drives for update bundles. Verifies signatures."""
        raw_bundles = self._scanner.scan()

        current_version = await self._get_config_value("update.current_version")
        validated = []

        for raw in raw_bundles:
            try:
                bundle = UpdateBundle(Path(raw["bundle_path"]))
                manifest = bundle.parse_manifest()

                # Check GPG signature
                sig_valid = False
                if raw.get("sig_path") and self._gpg.is_available():
                    try:
                        sig_valid = self._gpg.verify(raw["bundle_path"], raw["sig_path"])
                    except VaultError:
                        sig_valid = False
                elif not raw.get("sig_path"):
                    logger.warning("bundle_missing_signature", version=raw["version"])

                # Check version compatibility
                compatible = True
                try:
                    bundle_ver = semver.Version.parse(manifest.version)
                    current_ver = semver.Version.parse(current_version)
                    min_ver = semver.Version.parse(manifest.min_compatible_version)
                    compatible = current_ver >= min_ver and bundle_ver > current_ver
                except ValueError:
                    compatible = False

                validated.append({
                    "version": manifest.version,
                    "path": raw["bundle_path"],
                    "signature_valid": sig_valid,
                    "size_bytes": raw["size_bytes"],
                    "changelog": manifest.changelog,
                    "components": manifest.components,
                    "compatible": compatible,
                    "min_compatible_version": manifest.min_compatible_version,
                    "created_at": manifest.created_at,
                })

            except VaultError as e:
                logger.warning(
                    "bundle_validation_failed",
                    path=raw["bundle_path"],
                    error=e.message,
                )

        self._pending_bundles = validated

        return {
            "found": len(validated) > 0,
            "bundles": validated,
        }

    # ── Pending ──────────────────────────────────────────────────────────────

    async def get_pending(self) -> dict | None:
        """Details of the most recently scanned (and validated) bundle."""
        if not self._pending_bundles:
            return None
        # Return the best candidate (newest compatible version)
        compatible = [b for b in self._pending_bundles if b.get("compatible")]
        if compatible:
            return compatible[0]
        return self._pending_bundles[0] if self._pending_bundles else None

    # ── Apply ────────────────────────────────────────────────────────────────

    async def apply_update(
        self,
        confirmation: str,
        create_backup: bool = True,
        backup_passphrase: str | None = None,
        user_key_prefix: str | None = None,
    ) -> dict:
        """Start applying the pending update. Returns job ID for tracking."""
        if confirmation != "APPLY UPDATE":
            raise VaultError(
                code="confirmation_required",
                message="Confirmation text must be exactly 'APPLY UPDATE'.",
                status=400,
            )

        if self._engine.is_running:
            raise VaultError(
                code="update_in_progress",
                message="An update is already being applied.",
                status=409,
            )

        pending = await self.get_pending()
        if not pending:
            raise VaultError(
                code="no_pending_update",
                message="No pending update found. Run a scan first.",
                status=400,
            )

        if not pending.get("compatible"):
            raise VaultError(
                code="update_incompatible",
                message=f"Update {pending['version']} is not compatible with current version.",
                status=400,
            )

        current_version = await self._get_config_value("update.current_version")
        job_id = str(uuid.uuid4())

        # Create job record
        async with self._session_factory() as session:
            job = UpdateJob(
                id=job_id,
                status="pending",
                bundle_version=pending["version"],
                from_version=current_version,
                bundle_path=pending["path"],
                changelog=pending.get("changelog", ""),
                components_json=json.dumps(pending.get("components", {})),
            )
            session.add(job)

            # Audit log
            session.add(AuditLog(
                action="update_apply_started",
                user_key_prefix=user_key_prefix,
                details=json.dumps({
                    "job_id": job_id,
                    "from_version": current_version,
                    "to_version": pending["version"],
                }),
            ))
            await session.commit()

        # Launch background apply task
        asyncio.create_task(
            self._engine.apply(job_id, pending["path"], current_version)
        )

        return {
            "job_id": job_id,
            "status": "started",
            "message": f"Update {pending['version']} is being applied...",
        }

    # ── Progress ─────────────────────────────────────────────────────────────

    async def get_progress(self, job_id: str) -> dict:
        """Get current progress of an apply/rollback job."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(UpdateJob).where(UpdateJob.id == job_id)
            )
            job = result.scalar_one_or_none()

        if not job:
            raise NotFoundError(f"Update job '{job_id}' not found.")

        steps = []
        if job.steps_json:
            try:
                steps = json.loads(job.steps_json)
            except json.JSONDecodeError:
                pass

        log_entries = []
        if job.log_json:
            try:
                log_entries = json.loads(job.log_json)
            except json.JSONDecodeError:
                pass

        return {
            "job_id": job.id,
            "status": job.status,
            "bundle_version": job.bundle_version,
            "from_version": job.from_version,
            "progress_pct": job.progress_pct,
            "current_step": job.current_step,
            "steps": steps,
            "log_entries": log_entries,
            "error": job.error,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    # ── Rollback ─────────────────────────────────────────────────────────────

    async def rollback(
        self,
        confirmation: str,
        user_key_prefix: str | None = None,
    ) -> dict:
        """Rollback to the previous version."""
        if confirmation != "ROLLBACK UPDATE":
            raise VaultError(
                code="confirmation_required",
                message="Confirmation text must be exactly 'ROLLBACK UPDATE'.",
                status=400,
            )

        if not self._directory.has_rollback():
            raise VaultError(
                code="no_rollback_available",
                message="No rollback data available. A previous update must have been applied.",
                status=400,
            )

        if self._engine.is_running:
            raise VaultError(
                code="update_in_progress",
                message="Cannot rollback while an update is in progress.",
                status=409,
            )

        current_version = await self._get_config_value("update.current_version")

        # Read rollback version
        version_file = self._directory.rollback / "version.json"
        rollback_version = "unknown"
        if version_file.exists():
            try:
                data = json.loads(version_file.read_text())
                rollback_version = data.get("version", "unknown")
            except (json.JSONDecodeError, OSError):
                pass

        job_id = str(uuid.uuid4())

        # Create rollback job
        async with self._session_factory() as session:
            job = UpdateJob(
                id=job_id,
                status="pending",
                bundle_version=rollback_version,
                from_version=current_version,
            )
            session.add(job)

            session.add(AuditLog(
                action="update_rollback_started",
                user_key_prefix=user_key_prefix,
                details=json.dumps({
                    "job_id": job_id,
                    "from_version": current_version,
                    "rollback_to": rollback_version,
                }),
            ))
            await session.commit()

        # Launch background rollback
        asyncio.create_task(self._engine.rollback(job_id))

        return {
            "job_id": job_id,
            "status": "rollback_started",
            "rollback_to_version": rollback_version,
        }

    # ── History ──────────────────────────────────────────────────────────────

    async def get_history(self, offset: int = 0, limit: int = 20) -> dict:
        """Full update history from UpdateJob records."""
        async with self._session_factory() as session:
            count_result = await session.execute(
                select(func.count()).select_from(UpdateJob)
            )
            total = count_result.scalar() or 0

            result = await session.execute(
                select(UpdateJob)
                .order_by(UpdateJob.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            jobs = list(result.scalars().all())

        return {
            "updates": [
                {
                    "job_id": j.id,
                    "status": j.status,
                    "bundle_version": j.bundle_version,
                    "from_version": j.from_version,
                    "error": j.error,
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                    "created_at": j.created_at.isoformat() if j.created_at else None,
                }
                for j in jobs
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
        }
