"""Training data validation gate — requires quarantine Stage 4 clearance."""

import json
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import QuarantineFile, async_session as default_session_factory
from app.core.exceptions import NotFoundError, VaultError
from app.schemas.training import DatasetValidationResponse
from app.services.quarantine.checkers.training_analyzer import TrainingDataAnalyzer
from app.services.quarantine.checkers.training_validator import TrainingDataValidator

logger = structlog.get_logger()


async def validate_dataset(
    path: str,
    session_factory: async_sessionmaker | None = None,
) -> DatasetValidationResponse:
    """Validate a training dataset file.

    1. Verify the file exists and has .jsonl extension
    2. If it's a quarantine file ID, look up the record and verify status
    3. Run TrainingDataValidator + TrainingDataAnalyzer inline
    4. Return validation results
    """
    factory = session_factory or default_session_factory
    file_path = Path(path)

    # Check if path is a quarantine file ID (UUID format)
    if not file_path.exists() and len(path) == 36 and "-" in path:
        async with factory() as session:
            result = await session.execute(
                select(QuarantineFile).where(QuarantineFile.id == path)
            )
            qf = result.scalar_one_or_none()
            if qf is None:
                raise NotFoundError(f"File '{path}' not found (not a path or quarantine file ID).")

            # Verify quarantine status
            if qf.status not in ("clean", "approved"):
                raise VaultError(
                    code="quarantine_not_cleared",
                    message=f"File '{qf.original_filename}' has quarantine status '{qf.status}'. "
                    f"Only 'clean' or 'approved' files can be used for training.",
                    status=409,
                )

            # Use the sanitized/destination path
            file_path = Path(qf.sanitized_path or qf.destination_path or qf.quarantine_path or "")
            if not file_path.exists():
                raise NotFoundError(f"Quarantine file path does not exist: {file_path}")

    # Verify file exists
    if not file_path.exists():
        raise NotFoundError(f"Dataset file not found: {path}")

    # Verify extension
    if file_path.suffix.lower() != ".jsonl":
        raise VaultError(
            code="validation_error",
            message=f"Unsupported format: '{file_path.suffix}'. Only .jsonl files are supported for training.",
            status=400,
        )

    # Count records
    record_count = 0
    for line in file_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped:
            try:
                json.loads(stripped)
                record_count += 1
            except json.JSONDecodeError:
                pass

    # Run validators
    validator = TrainingDataValidator()
    analyzer = TrainingDataAnalyzer()

    validator_findings = await validator.validate(file_path, {})
    analyzer_findings = await analyzer.analyze(file_path, {})

    all_findings = [
        {"severity": f.severity, "code": f.code, "message": f.message, "details": f.details}
        for f in validator_findings + analyzer_findings
    ]

    # Determine format from validator
    detected_format = validator._detect_format(file_path)

    # Valid if no medium+ severity findings
    has_errors = any(f["severity"] in ("medium", "high", "critical") for f in all_findings)

    return DatasetValidationResponse(
        valid=not has_errors,
        format=detected_format if detected_format != "unknown" else None,
        record_count=record_count,
        findings=all_findings,
    )
