"""Stage 4: AI-specific safety checks for quarantine pipeline."""

from pathlib import Path

import structlog

from app.services.quarantine.stages import PipelineStage, StageFinding, StageResult
from app.services.quarantine.checkers.training_validator import TrainingDataValidator
from app.services.quarantine.checkers.training_analyzer import TrainingDataAnalyzer
from app.services.quarantine.checkers.poisoning_analyzer import DataPoisoningAnalyzer
from app.services.quarantine.checkers.injection_detector import PromptInjectionDetector
from app.services.quarantine.checkers.pii_scanner import PIIScanner
from app.services.quarantine.checkers.model_validator import ModelFileValidator

logger = structlog.get_logger()

# File extensions that trigger each checker
TRAINING_EXTENSIONS = {".jsonl"}
MODEL_EXTENSIONS = {".safetensors", ".gguf", ".pkl", ".pickle", ".bin", ".pt", ".pth", ".ckpt"}
TEXT_EXTENSIONS = {".txt", ".csv", ".json", ".jsonl", ".md"}
MODEL_CONFIG_NAMES = {"config.json"}


class AISafetyStage(PipelineStage):
    """Stage 4: AI-specific safety analysis.

    Routes files to appropriate sub-checkers based on extension:
    - .jsonl -> TrainingDataValidator, TrainingDataAnalyzer, DataPoisoningAnalyzer,
               PromptInjectionDetector, PIIScanner
    - .safetensors/.gguf/.pkl/etc -> ModelFileValidator
    - .txt/.csv/.json -> PIIScanner, PromptInjectionDetector
    """

    def __init__(self):
        self._training_validator = TrainingDataValidator()
        self._training_analyzer = TrainingDataAnalyzer()
        self._poisoning_analyzer = DataPoisoningAnalyzer()
        self._injection_detector = PromptInjectionDetector()
        self._pii_scanner = PIIScanner()
        self._model_validator = ModelFileValidator()

    @property
    def name(self) -> str:
        return "ai_safety"

    async def scan(self, file_path: Path, original_filename: str, config: dict) -> StageResult:
        """Run AI-specific checks based on file type."""
        # Master toggle
        if not config.get("ai_safety_enabled", True):
            return StageResult(passed=True)

        findings: list[StageFinding] = []
        ext = Path(original_filename).suffix.lower()
        basename = Path(original_filename).name.lower()

        try:
            # Training data files (.jsonl)
            if ext in TRAINING_EXTENSIONS:
                # Always validate structure
                findings.extend(await self._training_validator.validate(file_path, config))

                # Quality analysis
                findings.extend(await self._training_analyzer.analyze(file_path, config))

                # Poisoning detection
                findings.extend(await self._poisoning_analyzer.analyze(file_path, config))

                # Injection detection (if enabled)
                if config.get("injection_detection_enabled", True):
                    findings.extend(await self._injection_detector.scan(file_path, original_filename, config))

                # PII scanning (if enabled)
                if config.get("pii_enabled", True):
                    findings.extend(await self._pii_scanner.scan(file_path, original_filename, config))

            # Model files
            elif ext in MODEL_EXTENSIONS:
                if config.get("model_hash_verification", True):
                    findings.extend(await self._model_validator.validate(file_path, original_filename, config))

            # Model config files
            elif basename in MODEL_CONFIG_NAMES:
                if config.get("model_hash_verification", True):
                    findings.extend(await self._model_validator.validate(file_path, original_filename, config))

            # General text files (PII + injection)
            elif ext in TEXT_EXTENSIONS:
                if config.get("pii_enabled", True):
                    findings.extend(await self._pii_scanner.scan(file_path, original_filename, config))
                if config.get("injection_detection_enabled", True):
                    findings.extend(await self._injection_detector.scan(file_path, original_filename, config))

        except Exception as exc:
            logger.warning("ai_safety_checker_error", error=str(exc), file=original_filename)
            findings.append(StageFinding(
                stage="ai_safety",
                severity="medium",
                code="ai_safety_error",
                message=f"AI safety check encountered an error: {exc}",
            ))

        # Determine pass/fail based on max severity and PII action
        passed = self._determine_pass(findings, config)

        return StageResult(passed=passed, findings=findings)

    def _determine_pass(self, findings: list[StageFinding], config: dict) -> bool:
        """Determine if stage passes based on findings and config."""
        if not findings:
            return True

        severity_order = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        max_severity = max(severity_order.get(f.severity, 0) for f in findings)

        # Critical findings always fail
        if max_severity >= severity_order["critical"]:
            return False

        # High findings fail
        if max_severity >= severity_order["high"]:
            return False

        # PII action: "block" mode fails on any PII finding
        pii_action = config.get("pii_action", "flag")
        if pii_action == "block":
            pii_findings = [f for f in findings if f.code.startswith("pii_")]
            if pii_findings:
                pii_max = max(severity_order.get(f.severity, 0) for f in pii_findings)
                if pii_max >= severity_order["medium"]:
                    return False

        # Medium and below: pass (informational)
        return True
