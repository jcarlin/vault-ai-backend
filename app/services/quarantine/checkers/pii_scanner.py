"""PII scanner — detects personally identifiable information in training data."""

import csv
import io
import json
import re
from pathlib import Path

from app.services.quarantine.stages import StageFinding

try:
    import spacy

    _nlp = spacy.load("en_core_web_sm")
    _SPACY_AVAILABLE = True
except (ImportError, OSError):
    _SPACY_AVAILABLE = False

_SPACY_CHUNK_SIZE = 100_000  # 100KB chunks for NER


class PIIScanner:
    """Scans files for personally identifiable information."""

    def __init__(self) -> None:
        self._patterns: list[tuple[str, str, str, re.Pattern]] = [
            (
                "pii_ssn",
                "Social Security Number detected",
                "high",
                re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
            ),
            (
                "pii_credit_card",
                "Credit card number detected",
                "high",
                re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
            ),
            (
                "pii_phone",
                "Phone number detected",
                "medium",
                re.compile(
                    r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
                ),
            ),
            (
                "pii_email",
                "Email address detected",
                "medium",
                re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            ),
            (
                "pii_mrn",
                "Medical record number detected",
                "high",
                re.compile(r"\bMRN[:\s#]*\d{6,10}\b", re.IGNORECASE),
            ),
            (
                "pii_dob",
                "Date of birth detected",
                "medium",
                re.compile(
                    r"\b(?:DOB|Date of Birth|Born)[:\s]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b",
                    re.IGNORECASE,
                ),
            ),
        ]

    async def scan(
        self, file_path: Path, original_filename: str, config: dict
    ) -> list[StageFinding]:
        """Scan a file for PII patterns.

        Returns one StageFinding per PII type with match counts and sample locations.
        """
        text_lines = self._extract_text_lines(file_path, original_filename)
        if not text_lines:
            return []

        findings: list[StageFinding] = []

        # Regex-based PII detection
        for code, message, severity, pattern in self._patterns:
            match_count = 0
            sample_locations: list[int] = []

            for line_num, line in enumerate(text_lines, start=1):
                matches = pattern.findall(line)
                if not matches:
                    continue

                # For credit cards, apply Luhn validation
                if code == "pii_credit_card":
                    valid_count = sum(
                        1 for m in matches if self._luhn_check(m)
                    )
                    if valid_count == 0:
                        continue
                    match_count += valid_count
                else:
                    match_count += len(matches)

                if len(sample_locations) < 5:
                    sample_locations.append(line_num)

            if match_count > 0:
                findings.append(
                    StageFinding(
                        stage="ai_safety",
                        severity=severity,
                        code=code,
                        message=f"{message}: {match_count} occurrence(s).",
                        details={
                            "count": match_count,
                            "sample_locations": sample_locations,
                        },
                    )
                )

        # Optional spaCy NER
        if _SPACY_AVAILABLE:
            findings.extend(self._run_ner(text_lines))

        return findings

    def _luhn_check(self, number: str) -> bool:
        """Validate a credit card number using the Luhn algorithm."""
        digits = [int(d) for d in number if d.isdigit()]
        if len(digits) < 13 or len(digits) > 19:
            return False
        checksum = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            checksum += d
        return checksum % 10 == 0

    def _run_ner(self, text_lines: list[str]) -> list[StageFinding]:
        """Run spaCy NER to detect person names and addresses."""
        full_text = "\n".join(text_lines)
        findings: list[StageFinding] = []

        person_count = 0
        address_count = 0

        # Process in chunks
        for i in range(0, len(full_text), _SPACY_CHUNK_SIZE):
            chunk = full_text[i : i + _SPACY_CHUNK_SIZE]
            doc = _nlp(chunk)
            for ent in doc.ents:
                if ent.label_ == "PERSON":
                    person_count += 1
                elif ent.label_ == "GPE":
                    address_count += 1

        if person_count > 0:
            findings.append(
                StageFinding(
                    stage="ai_safety",
                    severity="medium",
                    code="pii_person_name",
                    message=f"Person name detected via NER: {person_count} occurrence(s).",
                    details={"count": person_count},
                )
            )

        if address_count > 0:
            findings.append(
                StageFinding(
                    stage="ai_safety",
                    severity="medium",
                    code="pii_address",
                    message=f"Geographic/address entity detected via NER: {address_count} occurrence(s).",
                    details={"count": address_count},
                )
            )

        return findings

    def _extract_text_lines(
        self, file_path: Path, original_filename: str
    ) -> list[str]:
        """Extract text lines from a file based on extension."""
        suffix = Path(original_filename).suffix.lower()

        if suffix == ".jsonl":
            return self._extract_jsonl(file_path)
        elif suffix == ".csv":
            return self._extract_csv(file_path)
        else:
            return self._read_text(file_path)

    def _extract_jsonl(self, file_path: Path) -> list[str]:
        """Extract text from JSONL fields."""
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
                lines.extend(self._extract_strings(record))
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

    def _extract_csv(self, file_path: Path) -> list[str]:
        """Read CSV file line by line."""
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

    def _read_text(self, file_path: Path) -> list[str]:
        """Read plain text / JSON file."""
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
