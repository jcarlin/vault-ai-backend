"""Update apply engine: multi-step orchestration with progress tracking."""

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import semver
import structlog
from sqlalchemy import select

import app.core.database as db_module
from app.config import settings
from app.core.database import SystemConfig, UpdateJob
from app.core.exceptions import VaultError
from app.services.update.bundle import UpdateBundle
from app.services.update.directory import UpdateDirectory
from app.services.update.gpg import GPGVerifier
from app.services.update.health import HealthChecker

logger = structlog.get_logger()

# Ordered list of apply steps
APPLY_STEPS = [
    "verify_signature",
    "create_backup",
    "extract_bundle",
    "verify_checksums",
    "run_migrations",
    "apply_signatures",
    "stage_code",
    "load_containers",
    "restart_services",
    "health_check",
]

# Progress weights (% of total) for each step
STEP_WEIGHTS = {
    "verify_signature": 5,
    "create_backup": 15,
    "extract_bundle": 10,
    "verify_checksums": 5,
    "run_migrations": 10,
    "apply_signatures": 5,
    "stage_code": 15,
    "load_containers": 10,
    "restart_services": 15,
    "health_check": 10,
}


class UpdateEngine:
    """Orchestrates multi-step update application with DB-tracked progress."""

    def __init__(
        self,
        directory: UpdateDirectory,
        gpg_verifier: GPGVerifier,
        session_factory=None,
    ):
        self._directory = directory
        self._gpg = gpg_verifier
        self._session_factory = session_factory or db_module.async_session
        self._active_job_id: str | None = None

    @property
    def is_running(self) -> bool:
        return self._active_job_id is not None

    async def apply(self, job_id: str, bundle_path: str, from_version: str) -> None:
        """Run the full apply sequence as a background task.

        This method is designed to be called via asyncio.create_task().
        Progress is tracked in the UpdateJob DB record.
        """
        if self._active_job_id is not None:
            raise VaultError(
                code="update_in_progress",
                message=f"An update is already in progress (job {self._active_job_id}).",
                status=409,
            )

        self._active_job_id = job_id
        bundle = UpdateBundle(Path(bundle_path))
        staging_dir = self._directory.staging_path(job_id)
        log_entries = []

        try:
            manifest = bundle.parse_manifest()
            steps_status = {s: "pending" for s in APPLY_STEPS}

            # Initialize job in DB
            await self._update_job(
                job_id,
                status="verifying",
                started_at=datetime.now(timezone.utc),
                steps_json=json.dumps(
                    [{"name": s, "status": "pending"} for s in APPLY_STEPS]
                ),
            )

            # ── Step 1: Verify GPG Signature ─────────────────────────────────
            await self._run_step(
                job_id, "verify_signature", steps_status, log_entries,
                self._step_verify_signature, bundle_path, manifest,
            )

            # ── Step 2: Create Backup ────────────────────────────────────────
            await self._run_step(
                job_id, "create_backup", steps_status, log_entries,
                self._step_create_backup, job_id,
            )

            # ── Step 3: Extract Bundle ───────────────────────────────────────
            await self._run_step(
                job_id, "extract_bundle", steps_status, log_entries,
                self._step_extract_bundle, bundle, staging_dir,
            )

            # ── Step 4: Verify Checksums ─────────────────────────────────────
            await self._run_step(
                job_id, "verify_checksums", steps_status, log_entries,
                self._step_verify_checksums, bundle, staging_dir,
            )

            # ── Step 5: Run Migrations ───────────────────────────────────────
            await self._run_step(
                job_id, "run_migrations", steps_status, log_entries,
                self._step_run_migrations, staging_dir, manifest,
            )

            # ── Step 6: Apply Signatures ─────────────────────────────────────
            await self._run_step(
                job_id, "apply_signatures", steps_status, log_entries,
                self._step_apply_signatures, staging_dir, manifest,
            )

            # ── Step 7: Stage Code ───────────────────────────────────────────
            await self._run_step(
                job_id, "stage_code", steps_status, log_entries,
                self._step_stage_code, staging_dir, manifest,
            )

            # ── Step 8: Load Containers ──────────────────────────────────────
            await self._run_step(
                job_id, "load_containers", steps_status, log_entries,
                self._step_load_containers, staging_dir, manifest,
            )

            # ── Step 9: Restart Services ─────────────────────────────────────
            await self._run_step(
                job_id, "restart_services", steps_status, log_entries,
                self._step_restart_services,
            )

            # ── Step 10: Health Check ────────────────────────────────────────
            await self._run_step(
                job_id, "health_check", steps_status, log_entries,
                self._step_health_check, job_id,
            )

            # ── All steps completed ──────────────────────────────────────────
            await self._update_job(
                job_id,
                status="completed",
                progress_pct=100,
                current_step="Update complete",
                completed_at=datetime.now(timezone.utc),
                log_json=json.dumps(log_entries),
                steps_json=json.dumps(
                    [{"name": s, "status": v} for s, v in steps_status.items()]
                ),
            )

            # Update system version
            await self._set_system_version(manifest.version)

            log_entries.append(self._log_entry("Update completed successfully"))
            logger.info("update_apply_completed", job_id=job_id, version=manifest.version)

        except VaultError as e:
            await self._update_job(
                job_id,
                status="failed",
                error=e.message,
                completed_at=datetime.now(timezone.utc),
                log_json=json.dumps(log_entries),
            )
            logger.error("update_apply_failed", job_id=job_id, error=e.message)
        except Exception as e:
            await self._update_job(
                job_id,
                status="failed",
                error=str(e),
                completed_at=datetime.now(timezone.utc),
                log_json=json.dumps(log_entries),
            )
            logger.exception("update_apply_unexpected_error", job_id=job_id)
        finally:
            self._active_job_id = None

    async def rollback(self, job_id: str) -> None:
        """Restore from rollback snapshot as a background task."""
        if self._active_job_id is not None:
            raise VaultError(
                code="update_in_progress",
                message="Cannot rollback while an update is in progress.",
                status=409,
            )

        if not self._directory.has_rollback():
            raise VaultError(
                code="no_rollback_available",
                message="No rollback data available.",
                status=400,
            )

        self._active_job_id = job_id
        log_entries = []

        try:
            await self._update_job(
                job_id,
                status="rolling_back",
                started_at=datetime.now(timezone.utc),
                progress_pct=10,
                current_step="Restoring previous version",
            )
            log_entries.append(self._log_entry("Starting rollback"))

            # Read rollback version info
            rollback_version_file = self._directory.rollback / "version.json"
            if rollback_version_file.exists():
                import json as _json
                version_data = _json.loads(rollback_version_file.read_text())
                rollback_version = version_data.get("version", "unknown")
            else:
                rollback_version = "unknown"

            await self._update_job(job_id, progress_pct=30, current_step="Triggering service restart")
            log_entries.append(self._log_entry("Triggering systemd rollback service"))

            # Trigger systemd rollback (same mechanism as apply)
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", "start", "vault-update-rollback.service",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
                if proc.returncode != 0:
                    logger.warning(
                        "rollback_systemd_not_available",
                        stderr=stderr.decode() if stderr else "",
                    )
                    log_entries.append(self._log_entry("Systemd rollback not available, manual intervention needed"))
            except (FileNotFoundError, asyncio.TimeoutError):
                log_entries.append(self._log_entry("Systemd not available for rollback"))

            await self._update_job(job_id, progress_pct=70, current_step="Running health checks")

            # Health check after rollback
            checker = HealthChecker(
                backend_retries=15,
                frontend_retries=10,
                caddy_retries=3,
            )
            health = await checker.check_all()

            if health["all_passed"]:
                # Update version back
                await self._set_system_version(rollback_version)
                await self._update_job(
                    job_id,
                    status="rolled_back",
                    progress_pct=100,
                    current_step="Rollback complete",
                    completed_at=datetime.now(timezone.utc),
                    log_json=json.dumps(log_entries),
                )
                log_entries.append(self._log_entry(f"Rolled back to {rollback_version}"))
                logger.info("rollback_completed", job_id=job_id, version=rollback_version)
            else:
                await self._update_job(
                    job_id,
                    status="failed",
                    error="Health checks failed after rollback",
                    completed_at=datetime.now(timezone.utc),
                    log_json=json.dumps(log_entries),
                )
                logger.error("rollback_health_check_failed", job_id=job_id, health=health)

        except Exception as e:
            await self._update_job(
                job_id,
                status="failed",
                error=str(e),
                completed_at=datetime.now(timezone.utc),
                log_json=json.dumps(log_entries),
            )
            logger.exception("rollback_failed", job_id=job_id)
        finally:
            self._active_job_id = None

    # ── Individual Apply Steps ───────────────────────────────────────────────

    async def _step_verify_signature(self, bundle_path: str, manifest) -> str:
        """Verify GPG signature of the bundle."""
        sig_path = bundle_path + ".sig" if not bundle_path.endswith(".sig") else bundle_path
        if not bundle_path.endswith(".sig"):
            sig_path = bundle_path.replace(".tar", ".tar.sig")

        if self._gpg.is_available():
            valid = self._gpg.verify(bundle_path, sig_path)
            if not valid:
                raise VaultError(
                    code="signature_invalid",
                    message="Bundle GPG signature verification failed.",
                    status=400,
                )
            return "GPG signature verified"
        else:
            return "GPG verification skipped (no key available)"

    async def _step_create_backup(self, job_id: str) -> str:
        """Create a backup before applying the update."""
        # Clean previous rollback data
        self._directory.cleanup_rollback()

        # Save current version info to rollback
        current_version = await self._get_system_version()
        version_data = {"version": current_version, "backed_up_at": datetime.now(timezone.utc).isoformat()}
        rollback_version_file = self._directory.rollback / "version.json"
        rollback_version_file.write_text(json.dumps(version_data, indent=2))

        # Copy current database as backup
        db_url = settings.vault_db_url
        if ":///" in db_url:
            db_path = Path(db_url.split(":///", 1)[1])
            if db_path.exists():
                backup_db = self._directory.rollback / "vault.db"
                shutil.copy2(db_path, backup_db)

        await self._update_job(job_id, backup_path=str(self._directory.rollback))
        return f"Backup created (version {current_version})"

    async def _step_extract_bundle(self, bundle: UpdateBundle, staging_dir: Path) -> str:
        """Extract bundle to staging directory."""
        bundle.extract_to(staging_dir)
        return f"Bundle extracted to staging"

    async def _step_verify_checksums(self, bundle: UpdateBundle, staging_dir: Path) -> str:
        """Verify per-file checksums."""
        # Find the extracted content directory (may be nested under version prefix)
        content_dir = staging_dir
        subdirs = [d for d in staging_dir.iterdir() if d.is_dir()]
        if len(subdirs) == 1 and (subdirs[0] / "manifest.json").exists():
            content_dir = subdirs[0]

        errors = bundle.verify_checksums(content_dir)
        if errors:
            raise VaultError(
                code="checksum_failed",
                message=f"Checksum verification failed: {'; '.join(errors[:3])}",
                status=400,
                details={"errors": errors},
            )
        return "All file checksums verified"

    async def _step_run_migrations(self, staging_dir: Path, manifest) -> str:
        """Run Alembic migrations if the bundle includes them."""
        if not manifest.components.get("migrations"):
            return "No migrations in this update (skipped)"

        content_dir = self._find_content_dir(staging_dir)
        migrations_dir = content_dir / "migrations"

        if not migrations_dir.exists():
            return "Migrations directory not found (skipped)"

        from app.core.migrations import _BACKEND_ROOT, run_upgrade_head

        alembic_versions = _BACKEND_ROOT / "alembic" / "versions"
        if not alembic_versions.exists():
            raise VaultError(
                code="migration_failed",
                message="Alembic versions directory not found",
                status=500,
            )

        for migration_file in migrations_dir.glob("*.py"):
            shutil.copy2(migration_file, alembic_versions / migration_file.name)

        try:
            await run_upgrade_head()
            return "Database migrations applied"
        except Exception as e:
            raise VaultError(
                code="migration_failed",
                message=f"Alembic migration failed: {e}",
                status=500,
            )

    async def _step_apply_signatures(self, staging_dir: Path, manifest) -> str:
        """Apply ClamAV/YARA signature updates."""
        if not manifest.components.get("signatures"):
            return "No signature updates (skipped)"

        content_dir = self._find_content_dir(staging_dir)
        signatures_dir = content_dir / "signatures"

        if not signatures_dir.exists():
            return "Signatures directory not found (skipped)"

        copied = 0
        # ClamAV signatures
        clamav_src = signatures_dir / "clamav"
        if clamav_src.exists():
            clamav_dst = Path("/var/lib/clamav")
            if clamav_dst.exists():
                for sig_file in clamav_src.iterdir():
                    shutil.copy2(sig_file, clamav_dst / sig_file.name)
                    copied += 1

        # YARA rules
        yara_src = signatures_dir / "yara_rules"
        if yara_src.exists():
            yara_dst = Path(settings.vault_yara_rules_dir)
            yara_dst.mkdir(parents=True, exist_ok=True)
            for rule_file in yara_src.iterdir():
                shutil.copy2(rule_file, yara_dst / rule_file.name)
                copied += 1

        return f"Applied {copied} signature file(s)"

    async def _step_stage_code(self, staging_dir: Path, manifest) -> str:
        """Stage backend/frontend code to the next deploy directory."""
        content_dir = self._find_content_dir(staging_dir)
        staged = []

        # Stage backend code
        if manifest.components.get("backend"):
            backend_src = content_dir / "backend"
            if backend_src.exists():
                backend_dst = self._directory.next_deploy / "backend"
                if backend_dst.exists():
                    shutil.rmtree(backend_dst)
                shutil.copytree(backend_src, backend_dst)
                staged.append("backend")

        # Stage frontend code
        if manifest.components.get("frontend"):
            frontend_src = content_dir / "frontend"
            if frontend_src.exists():
                frontend_dst = self._directory.next_deploy / "frontend"
                if frontend_dst.exists():
                    shutil.rmtree(frontend_dst)
                shutil.copytree(frontend_src, frontend_dst)
                staged.append("frontend")

        # Stage config files
        if manifest.components.get("config"):
            config_src = content_dir / "config"
            if config_src.exists():
                config_dst = self._directory.next_deploy / "config"
                if config_dst.exists():
                    shutil.rmtree(config_dst)
                shutil.copytree(config_src, config_dst)
                staged.append("config")

        if not staged:
            return "No code to stage (skipped)"
        return f"Staged: {', '.join(staged)}"

    async def _step_load_containers(self, staging_dir: Path, manifest) -> str:
        """Load OCI container images via docker load."""
        if not manifest.components.get("containers"):
            return "No container images (skipped)"

        content_dir = self._find_content_dir(staging_dir)
        containers_dir = content_dir / "containers"

        if not containers_dir.exists():
            return "Containers directory not found (skipped)"

        loaded = 0
        for image_file in containers_dir.glob("*.tar.gz"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "load", "-i", str(image_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
                if proc.returncode == 0:
                    loaded += 1
                    logger.info("container_image_loaded", image=image_file.name)
                else:
                    logger.warning(
                        "container_load_failed",
                        image=image_file.name,
                        stderr=stderr.decode()[:200],
                    )
            except (FileNotFoundError, asyncio.TimeoutError) as e:
                logger.warning("container_load_error", image=image_file.name, error=str(e))

        return f"Loaded {loaded} container image(s)"

    async def _step_restart_services(self) -> str:
        """Trigger systemd service to perform the atomic swap and restart."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "start", "vault-update-apply.service",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
            if proc.returncode != 0:
                logger.warning(
                    "systemd_apply_not_available",
                    stderr=stderr.decode() if stderr else "",
                )
                return "Systemd apply service not available (code staged for manual deploy)"
            return "Services restarted via systemd"
        except FileNotFoundError:
            return "Systemd not available (development mode)"
        except asyncio.TimeoutError:
            raise VaultError(
                code="restart_timeout",
                message="Service restart timed out after 180s",
                status=500,
            )

    async def _step_health_check(self, job_id: str) -> str:
        """Run post-update health checks."""
        checker = HealthChecker()
        results = await checker.check_all()

        if results["all_passed"]:
            return "All health checks passed"
        else:
            failed = [name for name, r in results.items() if name != "all_passed" and not r["passed"]]
            # Auto-rollback on health check failure
            logger.warning("health_check_failed_auto_rollback", failed=failed)
            raise VaultError(
                code="health_check_failed",
                message=f"Health checks failed for: {', '.join(failed)}. Automatic rollback recommended.",
                status=500,
                details={"failed_checks": failed, "results": results},
            )

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _run_step(
        self,
        job_id: str,
        step_name: str,
        steps_status: dict,
        log_entries: list,
        step_fn,
        *args,
    ) -> None:
        """Execute a step, update progress, handle errors."""
        steps_status[step_name] = "in_progress"
        progress = self._calculate_progress(steps_status)

        await self._update_job(
            job_id,
            status=step_name.replace("_", " ").title().replace(" ", "_").lower(),
            progress_pct=progress,
            current_step=step_name.replace("_", " ").title(),
            steps_json=json.dumps(
                [{"name": s, "status": v} for s, v in steps_status.items()]
            ),
            log_json=json.dumps(log_entries),
        )

        try:
            result_msg = await step_fn(*args)
            steps_status[step_name] = "completed"
            log_entries.append(self._log_entry(result_msg))
            logger.info("update_step_completed", step=step_name, message=result_msg)
        except VaultError:
            steps_status[step_name] = "failed"
            raise
        except Exception as e:
            steps_status[step_name] = "failed"
            raise VaultError(
                code=f"{step_name}_error",
                message=f"Step '{step_name}' failed: {e}",
                status=500,
            )

    def _calculate_progress(self, steps_status: dict) -> int:
        """Calculate overall progress percentage from step statuses."""
        completed_weight = sum(
            STEP_WEIGHTS.get(s, 0)
            for s, status in steps_status.items()
            if status == "completed"
        )
        in_progress_weight = sum(
            STEP_WEIGHTS.get(s, 0) // 2
            for s, status in steps_status.items()
            if status == "in_progress"
        )
        return min(completed_weight + in_progress_weight, 99)

    async def _update_job(self, job_id: str, **kwargs) -> None:
        """Update UpdateJob fields in the database."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(UpdateJob).where(UpdateJob.id == job_id)
            )
            job = result.scalar_one_or_none()
            if job:
                for key, value in kwargs.items():
                    if hasattr(job, key) and value is not None:
                        setattr(job, key, value)
                await session.commit()

    async def _get_system_version(self) -> str:
        """Read current system version from DB."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == "update.current_version")
            )
            row = result.scalar_one_or_none()
            return row.value if row else "1.0.0"

    async def _set_system_version(self, version: str) -> None:
        """Update system version in DB and version file."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == "update.current_version")
            )
            row = result.scalar_one_or_none()
            if row:
                row.value = version
            else:
                session.add(SystemConfig(key="update.current_version", value=version))

            # Also update last_update_at
            result2 = await session.execute(
                select(SystemConfig).where(SystemConfig.key == "update.last_update_at")
            )
            row2 = result2.scalar_one_or_none()
            now_str = datetime.now(timezone.utc).isoformat()
            if row2:
                row2.value = now_str
            else:
                session.add(SystemConfig(key="update.last_update_at", value=now_str))

            await session.commit()

        # Also write to version file
        version_file = Path(settings.vault_version_file_path)
        try:
            version_file.parent.mkdir(parents=True, exist_ok=True)
            version_file.write_text(json.dumps({
                "version": version,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2))
        except OSError:
            logger.warning("version_file_write_failed", path=str(version_file))

    def _find_content_dir(self, staging_dir: Path) -> Path:
        """Find the actual content directory inside staging (handles top-level prefix)."""
        subdirs = [d for d in staging_dir.iterdir() if d.is_dir()]
        if len(subdirs) == 1 and (subdirs[0] / "manifest.json").exists():
            return subdirs[0]
        return staging_dir

    @staticmethod
    def _log_entry(message: str) -> str:
        """Create a timestamped log entry."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        return f"[{ts}] {message}"
