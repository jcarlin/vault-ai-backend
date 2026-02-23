"""Data poisoning analyzer — repetition, backdoor patterns, statistical outliers."""

import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from app.services.quarantine.stages import StageFinding

MAX_SAMPLE = 10_000


class DataPoisoningAnalyzer:
    """Detects potential data poisoning: repetition, backdoors, statistical anomalies."""

    async def analyze(self, file_path: Path, config: dict) -> list[StageFinding]:
        records = self._load_records(file_path)
        if not records:
            return []

        fmt = self._detect_format(records[0])
        findings: list[StageFinding] = []

        findings.extend(self._check_trigram_stuffing(records, fmt))
        findings.extend(self._check_backdoor(records, fmt))
        findings.extend(self._check_statistical_outliers(records, fmt))

        return findings

    def _load_records(self, file_path: Path) -> list[dict]:
        records = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
                if len(records) >= MAX_SAMPLE:
                    break
        return records

    def _detect_format(self, record: dict) -> str:
        if "messages" in record and isinstance(record["messages"], list):
            return "chat"
        if "prompt" in record and "completion" in record:
            return "completion"
        if "instruction" in record:
            return "instruction"
        if "text" in record:
            return "text"
        return "unknown"

    def _extract_content(self, record: dict, fmt: str) -> str:
        if fmt == "chat":
            messages = record.get("messages", [])
            parts = []
            for msg in messages:
                if isinstance(msg, dict):
                    c = msg.get("content", "")
                    if isinstance(c, str):
                        parts.append(c)
            return " ".join(parts)
        elif fmt == "completion":
            return f"{record.get('prompt', '')} {record.get('completion', '')}"
        elif fmt == "instruction":
            parts = [record.get("instruction", "")]
            if "input" in record:
                parts.append(str(record.get("input", "")))
            if "output" in record:
                parts.append(str(record.get("output", "")))
            return " ".join(parts)
        elif fmt == "text":
            return record.get("text", "")
        return json.dumps(record, sort_keys=True)

    def _extract_output(self, record: dict, fmt: str) -> str | None:
        if fmt == "completion":
            return record.get("completion", "")
        elif fmt == "instruction":
            return record.get("output")
        elif fmt == "chat":
            messages = record.get("messages", [])
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    return msg.get("content", "")
        return None

    def _extract_input(self, record: dict, fmt: str) -> str | None:
        if fmt == "completion":
            return record.get("prompt", "")
        elif fmt == "instruction":
            return record.get("instruction", "")
        elif fmt == "chat":
            messages = record.get("messages", [])
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    return msg.get("content", "")
        return None

    def _get_trigrams(self, text: str) -> list[str]:
        words = text.lower().split()
        if len(words) < 3:
            return []
        return [" ".join(words[i:i+3]) for i in range(len(words) - 2)]

    def _check_trigram_stuffing(self, records: list[dict], fmt: str) -> list[StageFinding]:
        trigram_record_counts: dict[str, int] = defaultdict(int)
        total_records = len(records)

        for r in records:
            content = self._extract_content(r, fmt)
            trigrams = set(self._get_trigrams(content))
            for tg in trigrams:
                trigram_record_counts[tg] += 1

        if total_records == 0:
            return []

        for tg, count in trigram_record_counts.items():
            if count / total_records > 0.30:
                return [StageFinding(
                    stage="ai_safety",
                    severity="high",
                    code="poisoning_repetitive_content",
                    message=f"Trigram '{tg}' appears in {count}/{total_records} records (>{30}%).",
                    details={"trigram": tg, "count": count, "total": total_records,
                             "rate": round(count / total_records, 3)},
                )]
        return []

    def _check_backdoor(self, records: list[dict], fmt: str) -> list[StageFinding]:
        if fmt not in ("completion", "instruction", "chat"):
            return []

        output_hashes: dict[str, list[str]] = defaultdict(list)
        for r in records:
            output = self._extract_output(r, fmt)
            inp = self._extract_input(r, fmt)
            if output is None or inp is None:
                continue
            oh = hashlib.sha256(output.encode()).hexdigest()
            ih = hashlib.sha256(inp.encode()).hexdigest()
            output_hashes[oh].append(ih)

        total = len(records)
        if total == 0:
            return []

        for oh, input_hashes in output_hashes.items():
            unique_inputs = len(set(input_hashes))
            if len(input_hashes) / total > 0.20 and unique_inputs > 1:
                return [StageFinding(
                    stage="ai_safety",
                    severity="critical",
                    code="poisoning_backdoor_pattern",
                    message=f"Backdoor pattern: {len(input_hashes)} records ({len(input_hashes)}/{total}) share the same output despite {unique_inputs} different inputs.",
                    details={"shared_output_count": len(input_hashes), "unique_inputs": unique_inputs, "total": total},
                )]
        return []

    def _check_statistical_outliers(self, records: list[dict], fmt: str) -> list[StageFinding]:
        if len(records) < 5:
            return []

        features = []
        for r in records:
            content = self._extract_content(r, fmt)
            words = content.split()
            total_words = len(words)
            unique_words = len(set(words)) if total_words > 0 else 0
            punct_chars = sum(1 for c in content if c in ".,;:!?\"'()-[]{}@#$%^&*")
            total_chars = len(content)

            features.append({
                "length": total_chars,
                "unique_word_ratio": unique_words / total_words if total_words > 0 else 0,
                "punctuation_ratio": punct_chars / total_chars if total_chars > 0 else 0,
            })

        # Compute z-scores for each feature
        outlier_count = 0
        for key in ("length", "unique_word_ratio", "punctuation_ratio"):
            values = [f[key] for f in features]
            if len(values) < 2:
                continue
            mean = statistics.mean(values)
            stdev = statistics.stdev(values)
            if stdev == 0:
                continue
            for v in values:
                z = abs(v - mean) / stdev
                if z > 3:
                    outlier_count += 1
                    break  # Count at most one outlier per feature for the threshold check

        # Check if outlier records exceed 5% threshold
        # Recompute: count records where ANY z-score > 3
        outlier_records = 0
        for i, feat in enumerate(features):
            is_outlier = False
            for key in ("length", "unique_word_ratio", "punctuation_ratio"):
                values = [f[key] for f in features]
                mean = statistics.mean(values)
                stdev = statistics.stdev(values)
                if stdev == 0:
                    continue
                z = abs(feat[key] - mean) / stdev
                if z > 3:
                    is_outlier = True
                    break
            if is_outlier:
                outlier_records += 1

        total = len(records)
        if outlier_records / total > 0.05:
            return [StageFinding(
                stage="ai_safety",
                severity="medium",
                code="poisoning_statistical_outliers",
                message=f"Statistical outliers: {outlier_records}/{total} records have extreme feature values (z-score > 3).",
                details={"outlier_count": outlier_records, "total": total,
                         "rate": round(outlier_records / total, 3)},
            )]
        return []
