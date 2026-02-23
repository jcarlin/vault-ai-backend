"""Training data format validator — validates JSONL structure and field presence."""

import json
from collections import defaultdict
from pathlib import Path

from app.services.quarantine.stages import StageFinding

VALID_ROLES = {"system", "user", "assistant"}
MAX_FINDINGS_PER_CODE = 10


class TrainingDataValidator:
    """Validates JSONL training data for structural correctness."""

    async def validate(self, file_path: Path, config: dict) -> list[StageFinding]:
        findings: list[StageFinding] = []
        code_counts: dict[str, int] = defaultdict(int)

        detected_format = self._detect_format(file_path)
        if detected_format == "unknown":
            findings.append(StageFinding(
                stage="ai_safety",
                severity="medium",
                code="training_unknown_format",
                message="Could not detect training data format from first valid line.",
                details={"file": file_path.name},
            ))
            return findings

        line_num = 0
        for line in self._iter_lines(file_path):
            line_num += 1
            stripped = line.strip()
            if not stripped:
                continue

            # Parse JSON
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as e:
                self._add_capped(findings, code_counts, StageFinding(
                    stage="ai_safety",
                    severity="medium",
                    code="training_invalid_json",
                    message=f"Invalid JSON on line {line_num}: {e}",
                    details={"line": line_num},
                ))
                continue

            if not isinstance(record, dict):
                self._add_capped(findings, code_counts, StageFinding(
                    stage="ai_safety",
                    severity="medium",
                    code="training_invalid_json",
                    message=f"Line {line_num} is not a JSON object.",
                    details={"line": line_num},
                ))
                continue

            # Validate per detected format
            line_findings = self._validate_record(record, line_num, detected_format)
            for f in line_findings:
                self._add_capped(findings, code_counts, f)

        # Add summary findings for any codes that hit the cap
        for code, count in code_counts.items():
            if count > MAX_FINDINGS_PER_CODE:
                findings.append(StageFinding(
                    stage="ai_safety",
                    severity="low",
                    code=f"{code}_summary",
                    message=f"{count} total occurrences of {code} (showing first {MAX_FINDINGS_PER_CODE}).",
                    details={"total": count, "shown": MAX_FINDINGS_PER_CODE},
                ))

        return findings

    def _detect_format(self, file_path: Path) -> str:
        for line in self._iter_lines(file_path):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            if "messages" in record and isinstance(record["messages"], list):
                return "chat"
            if "prompt" in record and "completion" in record:
                return "completion"
            if "instruction" in record:
                return "instruction"
            if "text" in record:
                return "text"
            return "unknown"
        return "unknown"

    def _validate_record(self, record: dict, line_num: int, fmt: str) -> list[StageFinding]:
        if fmt == "chat":
            return self._validate_chat(record, line_num)
        elif fmt == "completion":
            return self._validate_completion(record, line_num)
        elif fmt == "instruction":
            return self._validate_instruction(record, line_num)
        elif fmt == "text":
            return self._validate_text(record, line_num)
        return []

    def _validate_chat(self, record: dict, line_num: int) -> list[StageFinding]:
        findings = []
        messages = record.get("messages")
        if not isinstance(messages, list):
            findings.append(StageFinding(
                stage="ai_safety",
                severity="medium",
                code="training_invalid_messages",
                message=f"Line {line_num}: 'messages' field is not a list.",
                details={"line": line_num},
            ))
            return findings

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                findings.append(StageFinding(
                    stage="ai_safety",
                    severity="medium",
                    code="training_missing_field",
                    message=f"Line {line_num}, message {i}: not a dict.",
                    details={"line": line_num, "message_index": i},
                ))
                continue

            role = msg.get("role")
            content = msg.get("content")

            if role is None or content is None:
                findings.append(StageFinding(
                    stage="ai_safety",
                    severity="medium",
                    code="training_missing_field",
                    message=f"Line {line_num}, message {i}: missing 'role' or 'content'.",
                    details={"line": line_num, "message_index": i},
                ))
                continue

            if role not in VALID_ROLES:
                findings.append(StageFinding(
                    stage="ai_safety",
                    severity="medium",
                    code="training_invalid_role",
                    message=f"Line {line_num}, message {i}: invalid role '{role}'.",
                    details={"line": line_num, "role": role},
                ))

            if isinstance(content, str) and content.strip() == "":
                findings.append(StageFinding(
                    stage="ai_safety",
                    severity="low",
                    code="training_empty_content",
                    message=f"Line {line_num}, message {i}: empty content.",
                    details={"line": line_num, "message_index": i},
                ))

        return findings

    def _validate_completion(self, record: dict, line_num: int) -> list[StageFinding]:
        findings = []
        if "prompt" not in record:
            findings.append(StageFinding(
                stage="ai_safety",
                severity="medium",
                code="training_missing_field",
                message=f"Line {line_num}: missing 'prompt' field.",
                details={"line": line_num},
            ))
        if "completion" not in record:
            findings.append(StageFinding(
                stage="ai_safety",
                severity="medium",
                code="training_missing_field",
                message=f"Line {line_num}: missing 'completion' field.",
                details={"line": line_num},
            ))
        for field in ("prompt", "completion"):
            val = record.get(field)
            if isinstance(val, str) and val.strip() == "":
                findings.append(StageFinding(
                    stage="ai_safety",
                    severity="low",
                    code="training_empty_content",
                    message=f"Line {line_num}: empty '{field}' field.",
                    details={"line": line_num, "field": field},
                ))
        return findings

    def _validate_instruction(self, record: dict, line_num: int) -> list[StageFinding]:
        findings = []
        if "instruction" not in record:
            findings.append(StageFinding(
                stage="ai_safety",
                severity="medium",
                code="training_missing_field",
                message=f"Line {line_num}: missing 'instruction' field.",
                details={"line": line_num},
            ))
        val = record.get("instruction")
        if isinstance(val, str) and val.strip() == "":
            findings.append(StageFinding(
                stage="ai_safety",
                severity="low",
                code="training_empty_content",
                message=f"Line {line_num}: empty 'instruction' field.",
                details={"line": line_num},
            ))
        return findings

    def _validate_text(self, record: dict, line_num: int) -> list[StageFinding]:
        findings = []
        if "text" not in record:
            findings.append(StageFinding(
                stage="ai_safety",
                severity="medium",
                code="training_missing_field",
                message=f"Line {line_num}: missing 'text' field.",
                details={"line": line_num},
            ))
        val = record.get("text")
        if isinstance(val, str) and val.strip() == "":
            findings.append(StageFinding(
                stage="ai_safety",
                severity="low",
                code="training_empty_content",
                message=f"Line {line_num}: empty 'text' field.",
                details={"line": line_num},
            ))
        return findings

    def _add_capped(
        self,
        findings: list[StageFinding],
        code_counts: dict[str, int],
        finding: StageFinding,
    ) -> None:
        code_counts[finding.code] += 1
        if code_counts[finding.code] <= MAX_FINDINGS_PER_CODE:
            findings.append(finding)

    @staticmethod
    def _iter_lines(file_path: Path):
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            yield from f
