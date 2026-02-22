"""Pipeline stage interface and shared data types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StageFinding:
    """A single finding from a pipeline stage."""

    stage: str
    severity: str  # none/low/medium/high/critical
    code: str
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class StageResult:
    """Result of running a pipeline stage on a single file."""

    passed: bool
    findings: list[StageFinding] = field(default_factory=list)
    sanitized_path: Path | None = None  # Set by Stage 3 if file was cleaned


class PipelineStage(ABC):
    """Abstract base class for quarantine pipeline stages."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stage identifier (e.g., 'file_integrity', 'malware_scan', 'sanitization')."""
        ...

    @abstractmethod
    async def scan(self, file_path: Path, original_filename: str, config: dict) -> StageResult:
        """Run this stage's checks on a file.

        Args:
            file_path: Path to the file in quarantine staging.
            original_filename: The original uploaded filename.
            config: Runtime quarantine config from SystemConfig.

        Returns:
            StageResult with pass/fail and any findings.
        """
        ...
