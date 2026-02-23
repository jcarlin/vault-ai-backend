"""Quarantine pipeline orchestrator — coordinates stages and manages jobs."""

import asyncio
import datetime
import hashlib
import json
import shutil
import uuid
from pathlib import Path

import structlog
from sqlalchemy import func, select

import app.core.database as db_module
from app.core.database import AuditLog, QuarantineFile, QuarantineJob, SystemConfig
from app.services.quarantine.directory import QuarantineDirectory
from app.services.quarantine.stages import PipelineStage, StageResult

logger = structlog.get_logger()

# Default quarantine config values (stored in SystemConfig table)
QUARANTINE_DEFAULTS = {
    "quarantine.max_file_size": "1073741824",  # 1 GB
    "quarantine.max_batch_files": "100",
    "quarantine.max_compression_ratio": "100",
    "quarantine.max_archive_depth": "3",
    "quarantine.auto_approve_clean": "true",
    "quarantine.strictness_level": "standard",  # standard/strict/paranoid
    "quarantine.ai_safety_enabled": "true",
    "quarantine.pii_enabled": "true",
    "quarantine.pii_action": "flag",  # flag/block/redact
    "quarantine.injection_detection_enabled": "true",
    "quarantine.model_hash_verification": "true",
}


class QuarantinePipeline:
    """Orchestrates the multi-stage quarantine scanning pipeline."""

    def __init__(
        self,
        directory: QuarantineDirectory | None = None,
        stages: list[PipelineStage] | None = None,
        session_factory=None,
    ):
        self._directory = directory or QuarantineDirectory()
        self._stages = stages or []
        self._session_factory = session_factory or db_module.async_session

    def set_stages(self, stages: list[PipelineStage]) -> None:
        """Set pipeline stages (called after all stages are initialized)."""
        self._stages = stages

    async def get_config(self) -> dict:
        """Load quarantine config from SystemConfig, populating defaults as needed."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key.startswith("quarantine."))
            )
            rows = {r.key: r.value for r in result.scalars().all()}

        # Populate any missing defaults
        if len(rows) < len(QUARANTINE_DEFAULTS):
            await self._populate_defaults()
            for key, value in QUARANTINE_DEFAULTS.items():
                rows.setdefault(key, value)

        return {
            "max_file_size": int(rows.get("quarantine.max_file_size", "1073741824")),
            "max_batch_files": int(rows.get("quarantine.max_batch_files", "100")),
            "max_compression_ratio": int(rows.get("quarantine.max_compression_ratio", "100")),
            "max_archive_depth": int(rows.get("quarantine.max_archive_depth", "3")),
            "auto_approve_clean": rows.get("quarantine.auto_approve_clean", "true").lower() == "true",
            "strictness_level": rows.get("quarantine.strictness_level", "standard"),
            "ai_safety_enabled": rows.get("quarantine.ai_safety_enabled", "true").lower() == "true",
            "pii_enabled": rows.get("quarantine.pii_enabled", "true").lower() == "true",
            "pii_action": rows.get("quarantine.pii_action", "flag"),
            "injection_detection_enabled": rows.get("quarantine.injection_detection_enabled", "true").lower() == "true",
            "model_hash_verification": rows.get("quarantine.model_hash_verification", "true").lower() == "true",
        }

    async def update_config(self, updates: dict) -> dict:
        """Update quarantine config in SystemConfig."""
        async with self._session_factory() as session:
            for field, value in updates.items():
                if value is None:
                    continue
                key = f"quarantine.{field}"
                if key not in QUARANTINE_DEFAULTS:
                    continue  # Ignore unknown keys
                if isinstance(value, bool):
                    stored = "true" if value else "false"
                else:
                    stored = str(value)
                existing = await session.execute(
                    select(SystemConfig).where(SystemConfig.key == key)
                )
                row = existing.scalar_one_or_none()
                if row:
                    row.value = stored
                else:
                    session.add(SystemConfig(key=key, value=stored))
            await session.commit()
        return await self.get_config()

    async def submit_scan(
        self,
        files: list[tuple[str, bytes]],  # (filename, content) pairs
        source_type: str = "upload",
        submitted_by: str | None = None,
    ) -> str:
        """Submit files for scanning. Returns job ID.

        Creates job + file records, copies files to staging, launches background scan.
        """
        config = await self.get_config()
        max_batch = config["max_batch_files"]
        max_size = config["max_file_size"]

        if len(files) > max_batch:
            from app.core.exceptions import VaultError
            raise VaultError(
                code="batch_too_large",
                message=f"Batch exceeds maximum of {max_batch} files.",
                status=400,
            )

        job_id = str(uuid.uuid4())

        # Create job record
        async with self._session_factory() as session:
            job = QuarantineJob(
                id=job_id,
                status="pending",
                total_files=len(files),
                source_type=source_type,
                submitted_by=submitted_by,
            )
            session.add(job)

            # Create file records and write to staging
            for filename, content in files:
                if len(content) > max_size:
                    from app.core.exceptions import VaultError
                    raise VaultError(
                        code="file_too_large",
                        message=f"File '{filename}' exceeds maximum size of {max_size} bytes.",
                        status=400,
                    )

                file_id = str(uuid.uuid4())
                sha256 = hashlib.sha256(content).hexdigest()
                staging_path = self._directory.staging_path(job_id, file_id, filename)
                staging_path.write_bytes(content)

                file_record = QuarantineFile(
                    id=file_id,
                    job_id=job_id,
                    original_filename=filename,
                    file_size=len(content),
                    sha256_hash=sha256,
                    status="pending",
                    quarantine_path=str(staging_path),
                )
                session.add(file_record)

            await session.commit()

        # Launch background scanning task
        asyncio.create_task(self._run_pipeline(job_id))
        return job_id

    async def submit_scan_path(
        self,
        scan_path: str,
        source_type: str = "usb_path",
        submitted_by: str | None = None,
    ) -> str:
        """Submit a directory path for scanning (e.g., USB mount). Returns job ID."""
        source = Path(scan_path)
        if not source.exists():
            from app.core.exceptions import NotFoundError
            raise NotFoundError(f"Path '{scan_path}' not found.")

        if source.is_file():
            files_to_scan = [(source.name, source.read_bytes())]
        elif source.is_dir():
            files_to_scan = []
            for f in source.rglob("*"):
                if f.is_file():
                    files_to_scan.append((str(f.relative_to(source)), f.read_bytes()))
        else:
            from app.core.exceptions import VaultError
            raise VaultError(code="invalid_path", message="Path must be a file or directory.", status=400)

        return await self.submit_scan(files_to_scan, source_type=source_type, submitted_by=submitted_by)

    async def get_job_status(self, job_id: str) -> dict:
        """Get scan progress for a job including per-file status."""
        async with self._session_factory() as session:
            job_result = await session.execute(
                select(QuarantineJob).where(QuarantineJob.id == job_id)
            )
            job = job_result.scalar_one_or_none()
            if job is None:
                from app.core.exceptions import NotFoundError
                raise NotFoundError(f"Scan job '{job_id}' not found.")

            files_result = await session.execute(
                select(QuarantineFile).where(QuarantineFile.job_id == job_id)
            )
            files = list(files_result.scalars().all())

        return {
            "id": job.id,
            "status": job.status,
            "total_files": job.total_files,
            "files_completed": job.files_completed,
            "files_flagged": job.files_flagged,
            "files_clean": job.files_clean,
            "source_type": job.source_type,
            "submitted_by": job.submitted_by,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "files": [self._file_to_dict(f) for f in files],
        }

    async def list_held_files(self, offset: int = 0, limit: int = 20) -> dict:
        """List files in 'held' status, paginated."""
        async with self._session_factory() as session:
            # Count total
            count_result = await session.execute(
                select(func.count()).select_from(QuarantineFile).where(QuarantineFile.status == "held")
            )
            total = count_result.scalar() or 0

            # Fetch page
            result = await session.execute(
                select(QuarantineFile)
                .where(QuarantineFile.status == "held")
                .order_by(QuarantineFile.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            files = list(result.scalars().all())

        return {
            "files": [self._file_to_dict(f) for f in files],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    async def get_held_file(self, file_id: str) -> dict:
        """Get details for a single held file."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(QuarantineFile).where(QuarantineFile.id == file_id)
            )
            f = result.scalar_one_or_none()
            if f is None:
                from app.core.exceptions import NotFoundError
                raise NotFoundError(f"Quarantine file '{file_id}' not found.")
        return self._file_to_dict(f)

    async def approve_file(self, file_id: str, reason: str, reviewed_by: str | None = None) -> dict:
        """Approve a held file — move to production storage + audit log."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(QuarantineFile).where(QuarantineFile.id == file_id)
            )
            f = result.scalar_one_or_none()
            if f is None:
                from app.core.exceptions import NotFoundError
                raise NotFoundError(f"Quarantine file '{file_id}' not found.")
            if f.status != "held":
                from app.core.exceptions import VaultError
                raise VaultError(
                    code="invalid_status",
                    message=f"File status is '{f.status}', not 'held'. Only held files can be approved.",
                    status=409,
                )

            f.status = "approved"
            f.review_reason = reason
            f.reviewed_by = reviewed_by
            f.reviewed_at = datetime.datetime.utcnow()

            # Move sanitized file (if exists) or original to destination
            source_path = Path(f.sanitized_path) if f.sanitized_path else Path(f.quarantine_path) if f.quarantine_path else None
            if source_path and source_path.exists() and f.destination_path:
                dest = Path(f.destination_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copy2, source_path, dest)

            # Audit log
            session.add(AuditLog(
                action="quarantine_approve",
                user_key_prefix=reviewed_by,
                details=json.dumps({"file_id": file_id, "filename": f.original_filename, "reason": reason}),
            ))

            await session.commit()
            await session.refresh(f)

        return self._file_to_dict(f)

    async def reject_file(self, file_id: str, reason: str, reviewed_by: str | None = None) -> dict:
        """Reject a held file — delete from quarantine + audit log."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(QuarantineFile).where(QuarantineFile.id == file_id)
            )
            f = result.scalar_one_or_none()
            if f is None:
                from app.core.exceptions import NotFoundError
                raise NotFoundError(f"Quarantine file '{file_id}' not found.")
            if f.status != "held":
                from app.core.exceptions import VaultError
                raise VaultError(
                    code="invalid_status",
                    message=f"File status is '{f.status}', not 'held'. Only held files can be rejected.",
                    status=409,
                )

            f.status = "rejected"
            f.review_reason = reason
            f.reviewed_by = reviewed_by
            f.reviewed_at = datetime.datetime.utcnow()

            # Delete files from disk
            for path_str in [f.quarantine_path, f.sanitized_path]:
                if path_str:
                    p = Path(path_str)
                    if p.exists():
                        p.unlink()

            # Audit log
            session.add(AuditLog(
                action="quarantine_reject",
                user_key_prefix=reviewed_by,
                details=json.dumps({"file_id": file_id, "filename": f.original_filename, "reason": reason}),
            ))

            await session.commit()
            await session.refresh(f)

        return self._file_to_dict(f)

    async def get_stats(self) -> dict:
        """Aggregate quarantine statistics."""
        async with self._session_factory() as session:
            # Job stats
            job_count = await session.scalar(
                select(func.count()).select_from(QuarantineJob)
            ) or 0
            jobs_completed = await session.scalar(
                select(func.count()).select_from(QuarantineJob).where(QuarantineJob.status == "completed")
            ) or 0

            # File stats
            total_files = await session.scalar(
                select(func.count()).select_from(QuarantineFile)
            ) or 0
            files_clean = await session.scalar(
                select(func.count()).select_from(QuarantineFile).where(QuarantineFile.status == "clean")
            ) or 0
            files_held = await session.scalar(
                select(func.count()).select_from(QuarantineFile).where(QuarantineFile.status == "held")
            ) or 0
            files_approved = await session.scalar(
                select(func.count()).select_from(QuarantineFile).where(QuarantineFile.status == "approved")
            ) or 0
            files_rejected = await session.scalar(
                select(func.count()).select_from(QuarantineFile).where(QuarantineFile.status == "rejected")
            ) or 0

            # Severity distribution
            severity_rows = await session.execute(
                select(QuarantineFile.risk_severity, func.count())
                .group_by(QuarantineFile.risk_severity)
            )
            severity_dist = {row[0]: row[1] for row in severity_rows}

        return {
            "total_jobs": job_count,
            "jobs_completed": jobs_completed,
            "total_files_scanned": total_files,
            "files_clean": files_clean,
            "files_held": files_held,
            "files_approved": files_approved,
            "files_rejected": files_rejected,
            "severity_distribution": severity_dist,
        }

    async def get_signature_info(self) -> dict:
        """Get ClamAV/YARA signature versions and freshness."""
        import os

        info = {"clamav": {"available": False}, "yara": {"available": False}, "blacklist": {"available": False}}

        # ClamAV signatures
        clamav_dir = self._directory.signatures_clamav
        if clamav_dir.exists():
            sig_files = list(clamav_dir.glob("*.cvd")) + list(clamav_dir.glob("*.cld"))
            if sig_files:
                newest = max(sig_files, key=lambda p: p.stat().st_mtime)
                mtime = datetime.datetime.fromtimestamp(newest.stat().st_mtime)
                age_hours = (datetime.datetime.utcnow() - mtime).total_seconds() / 3600
                info["clamav"] = {
                    "available": True,
                    "last_updated": mtime.isoformat(),
                    "age_hours": round(age_hours, 1),
                    "freshness": "fresh" if age_hours < 24 else "stale" if age_hours < 168 else "outdated",
                    "file_count": len(sig_files),
                }

        # YARA rules
        yara_dir = self._directory.signatures_yara
        if yara_dir.exists():
            rule_files = list(yara_dir.glob("*.yar")) + list(yara_dir.glob("*.yara"))
            if rule_files:
                newest = max(rule_files, key=lambda p: p.stat().st_mtime)
                mtime = datetime.datetime.fromtimestamp(newest.stat().st_mtime)
                age_hours = (datetime.datetime.utcnow() - mtime).total_seconds() / 3600
                info["yara"] = {
                    "available": True,
                    "last_updated": mtime.isoformat(),
                    "age_hours": round(age_hours, 1),
                    "freshness": "fresh" if age_hours < 24 else "stale" if age_hours < 168 else "outdated",
                    "rule_count": len(rule_files),
                }

        # Blacklist
        bl_path = self._directory.blacklist_path
        if bl_path.exists():
            mtime = datetime.datetime.fromtimestamp(bl_path.stat().st_mtime)
            try:
                data = json.loads(bl_path.read_text())
                count = len(data.get("hashes", []))
            except Exception:
                count = 0
            info["blacklist"] = {
                "available": True,
                "last_updated": mtime.isoformat(),
                "hash_count": count,
            }

        return info

    # ── Internal pipeline execution ──────────────────────────────────────

    async def _run_pipeline(self, job_id: str) -> None:
        """Background task: run all pipeline stages on each file in the job."""
        config = await self.get_config()

        async with self._session_factory() as session:
            # Mark job as scanning
            job_result = await session.execute(
                select(QuarantineJob).where(QuarantineJob.id == job_id)
            )
            job = job_result.scalar_one_or_none()
            if job is None:
                return
            job.status = "scanning"
            await session.commit()

            # Get all files for this job
            files_result = await session.execute(
                select(QuarantineFile).where(QuarantineFile.job_id == job_id)
            )
            files = list(files_result.scalars().all())

        files_clean = 0
        files_flagged = 0
        files_completed = 0

        for file_record in files:
            try:
                await self._scan_single_file(file_record.id, config)
            except Exception:
                logger.exception("quarantine_file_scan_error", file_id=file_record.id)
                # Mark as held on unexpected error
                async with self._session_factory() as session:
                    fr = await session.scalar(
                        select(QuarantineFile).where(QuarantineFile.id == file_record.id)
                    )
                    if fr:
                        fr.status = "held"
                        fr.risk_severity = "high"
                        fr.review_reason = "Unexpected error during scanning"
                        await session.commit()
                files_flagged += 1

            # Update counts
            files_completed += 1
            async with self._session_factory() as session:
                fr = await session.scalar(
                    select(QuarantineFile).where(QuarantineFile.id == file_record.id)
                )
                if fr:
                    if fr.status == "clean" or fr.status == "approved":
                        files_clean += 1
                    elif fr.status == "held":
                        files_flagged += 1

                # Update job progress
                j = await session.scalar(
                    select(QuarantineJob).where(QuarantineJob.id == job_id)
                )
                if j:
                    j.files_completed = files_completed
                    j.files_clean = files_clean
                    j.files_flagged = files_flagged
                    await session.commit()

        # Mark job complete
        async with self._session_factory() as session:
            j = await session.scalar(
                select(QuarantineJob).where(QuarantineJob.id == job_id)
            )
            if j:
                j.status = "completed"
                j.completed_at = datetime.datetime.utcnow()
                j.files_completed = files_completed
                j.files_clean = files_clean
                j.files_flagged = files_flagged
                await session.commit()

        logger.info(
            "quarantine_job_complete",
            job_id=job_id,
            total=len(files),
            clean=files_clean,
            flagged=files_flagged,
        )

    async def _scan_single_file(self, file_id: str, config: dict) -> None:
        """Run all stages sequentially on a single file."""
        async with self._session_factory() as session:
            fr = await session.scalar(
                select(QuarantineFile).where(QuarantineFile.id == file_id)
            )
            if fr is None:
                return
            fr.status = "scanning"
            await session.commit()

        file_path = Path(fr.quarantine_path) if fr.quarantine_path else None
        if file_path is None or not file_path.exists():
            return

        all_findings = []
        max_severity = "none"
        severity_order = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        sanitized_path = None

        for stage in self._stages:
            async with self._session_factory() as session:
                fr = await session.scalar(
                    select(QuarantineFile).where(QuarantineFile.id == file_id)
                )
                if fr:
                    fr.current_stage = stage.name
                    await session.commit()

            # Run stage in thread for CPU-bound work
            result: StageResult = await asyncio.to_thread(
                self._run_stage_sync, stage, file_path, fr.original_filename, config
            )

            for finding in result.findings:
                all_findings.append({
                    "stage": finding.stage,
                    "severity": finding.severity,
                    "code": finding.code,
                    "message": finding.message,
                    "details": finding.details,
                })
                if severity_order.get(finding.severity, 0) > severity_order.get(max_severity, 0):
                    max_severity = finding.severity

            if result.sanitized_path:
                sanitized_path = result.sanitized_path

            if not result.passed:
                # Stage failed — hold file
                async with self._session_factory() as session:
                    fr = await session.scalar(
                        select(QuarantineFile).where(QuarantineFile.id == file_id)
                    )
                    if fr:
                        fr.status = "held"
                        fr.risk_severity = max_severity
                        fr.findings_json = json.dumps(all_findings)
                        fr.review_reason = f"Failed {stage.name} stage"
                        if sanitized_path:
                            fr.sanitized_path = str(sanitized_path)
                        # Move to held directory
                        held_path = self._directory.held_path(file_id, fr.original_filename)
                        if file_path.exists():
                            await asyncio.to_thread(shutil.copy2, file_path, held_path)
                        await session.commit()
                return  # Stop pipeline for this file

        # All stages passed
        async with self._session_factory() as session:
            fr = await session.scalar(
                select(QuarantineFile).where(QuarantineFile.id == file_id)
            )
            if fr:
                fr.findings_json = json.dumps(all_findings)
                fr.risk_severity = max_severity
                fr.current_stage = "complete"
                if sanitized_path:
                    fr.sanitized_path = str(sanitized_path)
                if config.get("auto_approve_clean", True):
                    fr.status = "clean"
                else:
                    fr.status = "held"
                    fr.review_reason = "Manual review required (auto_approve_clean=false)"
                await session.commit()

    @staticmethod
    def _run_stage_sync(stage: PipelineStage, file_path: Path, original_filename: str, config: dict) -> StageResult:
        """Synchronous wrapper — called via asyncio.to_thread()."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(stage.scan(file_path, original_filename, config))
        finally:
            loop.close()

    @staticmethod
    def _file_to_dict(f: QuarantineFile) -> dict:
        findings = []
        if f.findings_json:
            try:
                findings = json.loads(f.findings_json)
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "id": f.id,
            "job_id": f.job_id,
            "original_filename": f.original_filename,
            "file_size": f.file_size,
            "mime_type": f.mime_type,
            "sha256_hash": f.sha256_hash,
            "status": f.status,
            "current_stage": f.current_stage,
            "risk_severity": f.risk_severity,
            "findings": findings,
            "quarantine_path": f.quarantine_path,
            "sanitized_path": f.sanitized_path,
            "destination_path": f.destination_path,
            "review_reason": f.review_reason,
            "reviewed_by": f.reviewed_by,
            "reviewed_at": f.reviewed_at.isoformat() if f.reviewed_at else None,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "updated_at": f.updated_at.isoformat() if f.updated_at else None,
        }

    # ── Defaults ─────────────────────────────────────────────────────────

    async def _populate_defaults(self) -> None:
        async with self._session_factory() as session:
            for key, value in QUARANTINE_DEFAULTS.items():
                existing = await session.execute(
                    select(SystemConfig).where(SystemConfig.key == key)
                )
                if existing.scalar_one_or_none() is None:
                    session.add(SystemConfig(key=key, value=value))
            await session.commit()
