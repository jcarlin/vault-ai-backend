"""Prompt injection detector — scans training data for injection attacks."""

import json
import re
from pathlib import Path

from app.services.quarantine.stages import StageFinding


class PromptInjectionDetector:
    """Detects prompt injection patterns in training data files."""

    def __init__(self) -> None:
        flags = re.IGNORECASE

        self._patterns: list[tuple[str, str, re.Pattern]] = [
            # Direct override
            (
                "injection_override",
                "Prompt override attempt detected",
                re.compile(
                    r"(?:"
                    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions"
                    r"|disregard\s+your\s+instructions"
                    r"|forget\s+everything\s+above"
                    r"|override\s+your\s+programming"
                    r")",
                    flags,
                ),
            ),
            # Role hijacking
            (
                "injection_role_hijack",
                "Role hijacking attempt detected",
                re.compile(
                    r"(?:"
                    r"you\s+are\s+now\s+a"
                    r"|act\s+as\s+if\s+you\s+are"
                    r"|pretend\s+to\s+be"
                    r"|switch\s+to"
                    r"|new\s+persona"
                    r")",
                    flags,
                ),
            ),
            # Prompt extraction
            (
                "injection_prompt_extraction",
                "Prompt extraction attempt detected",
                re.compile(
                    r"(?:"
                    r"show\s+me\s+your\s+system\s+prompt"
                    r"|what\s+are\s+your\s+instructions"
                    r"|repeat\s+your\s+initial\s+prompt"
                    r"|display\s+your\s+rules"
                    r"|reveal\s+your\s+prompt"
                    r")",
                    flags,
                ),
            ),
            # Delimiter injection
            (
                "injection_delimiter",
                "Delimiter injection detected",
                re.compile(
                    r"(?:"
                    r"```(?:system|assistant)\b"
                    r"|<(?:system|assistant)\b[^>]*>"
                    r")",
                    flags,
                ),
            ),
            # Chat template injection
            (
                "injection_template",
                "Chat template injection detected",
                re.compile(
                    r"(?:"
                    r"\[INST\]"
                    r"|\[/INST\]"
                    r"|<\|im_start\|>"
                    r"|<\|im_end\|>"
                    r"|<<SYS>>"
                    r"|<</SYS>>"
                    r"|<\|system\|>"
                    r"|<\|user\|>"
                    r"|<\|assistant\|>"
                    r")",
                    flags,
                ),
            ),
            # Known jailbreaks
            (
                "injection_jailbreak",
                "Known jailbreak pattern detected",
                re.compile(
                    r"(?:"
                    r"\bDAN\b"
                    r"|Do\s+Anything\s+Now"
                    r"|developer\s+mode"
                    r"|jailbreak"
                    r"|bypass\s+safety"
                    r"|ignore\s+safety"
                    r")",
                    flags,
                ),
            ),
        ]

    async def scan(
        self, file_path: Path, original_filename: str, config: dict
    ) -> list[StageFinding]:
        """Scan a file for prompt injection patterns.

        Returns one StageFinding per pattern category with match counts.
        """
        # Extract text lines from file
        lines = self._extract_lines(file_path, original_filename)
        if not lines:
            return []

        total_lines = len(lines)

        # Count matches per pattern category
        category_counts: dict[str, int] = {}
        for code, _, _ in self._patterns:
            category_counts[code] = 0

        for line in lines:
            for code, _, pattern in self._patterns:
                if pattern.search(line):
                    category_counts[code] += 1

        # Build findings
        findings: list[StageFinding] = []
        total_hits = sum(category_counts.values())

        for code, message, _ in self._patterns:
            count = category_counts[code]
            if count == 0:
                continue

            severity = self._escalate_severity(count, total_hits, total_lines)
            findings.append(
                StageFinding(
                    stage="ai_safety",
                    severity=severity,
                    code=code,
                    message=f"{message}: {count} occurrence(s) in {total_lines} lines.",
                    details={"count": count, "total_lines": total_lines},
                )
            )

        return findings

    def _escalate_severity(
        self, category_count: int, total_hits: int, total_lines: int
    ) -> str:
        """Determine severity based on prevalence."""
        if total_lines > 0 and total_hits / total_lines > 0.20:
            return "critical"
        if total_lines > 0 and total_hits / total_lines > 0.05:
            return "high"
        if total_hits > 5:
            return "high"
        if total_hits >= 3:
            return "medium"
        # Isolated hits
        return "low"

    def _extract_lines(self, file_path: Path, original_filename: str) -> list[str]:
        """Extract text lines from a file."""
        suffix = Path(original_filename).suffix.lower()

        if suffix == ".jsonl":
            return self._extract_jsonl_lines(file_path)
        else:
            return self._read_text_lines(file_path)

    def _extract_jsonl_lines(self, file_path: Path) -> list[str]:
        """Parse JSONL, extract all string values from each record."""
        lines: list[str] = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    lines.append(stripped)
                    continue
                # Recursively extract string values
                texts = self._extract_strings(record)
                lines.extend(texts)
        return lines

    def _extract_strings(self, obj: object) -> list[str]:
        """Recursively extract all string values from a JSON structure."""
        results: list[str] = []
        if isinstance(obj, str):
            results.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                results.extend(self._extract_strings(v))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(self._extract_strings(item))
        return results

    def _read_text_lines(self, file_path: Path) -> list[str]:
        """Read plain text file line by line."""
        lines: list[str] = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        lines.append(stripped)
        except (OSError, UnicodeDecodeError):
            pass
        return lines
