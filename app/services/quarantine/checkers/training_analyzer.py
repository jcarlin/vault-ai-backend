"""Training data quality analyzer — duplicates, length distribution, class balance."""

import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from app.services.quarantine.stages import StageFinding

MAX_SAMPLE = 10_000


class TrainingDataAnalyzer:
    """Analyzes training data quality: duplicates, length stats, class balance."""

    async def analyze(self, file_path: Path, config: dict) -> list[StageFinding]:
        records = self._load_records(file_path)
        if not records:
            return []

        fmt = self._detect_format(records[0])
        findings: list[StageFinding] = []

        findings.extend(self._check_duplicates(records))
        findings.extend(self._check_length_distribution(records, fmt))
        findings.extend(self._check_class_balance(records, fmt))

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
            prompt = record.get("prompt", "")
            completion = record.get("completion", "")
            return f"{prompt} {completion}"
        elif fmt == "instruction":
            parts = [record.get("instruction", "")]
            if "input" in record:
                parts.append(record.get("input", ""))
            if "output" in record:
                parts.append(record.get("output", ""))
            return " ".join(str(p) for p in parts)
        elif fmt == "text":
            return record.get("text", "")
        return json.dumps(record, sort_keys=True)

    def _check_duplicates(self, records: list[dict]) -> list[StageFinding]:
        hashes = []
        for r in records:
            serialized = json.dumps(r, sort_keys=True)
            h = hashlib.sha256(serialized.encode()).hexdigest()
            hashes.append(h)

        total = len(hashes)
        unique = len(set(hashes))
        duplicate_count = total - unique
        duplicate_rate = duplicate_count / total if total > 0 else 0

        if duplicate_rate > 0.10:
            return [StageFinding(
                stage="ai_safety",
                severity="medium",
                code="training_high_duplicate_rate",
                message=f"High duplicate rate: {duplicate_rate:.0%} of records are duplicates ({duplicate_count}/{total}).",
                details={"duplicate_rate": round(duplicate_rate, 3), "total": total, "duplicates": duplicate_count},
            )]
        return []

    def _check_length_distribution(self, records: list[dict], fmt: str) -> list[StageFinding]:
        findings = []
        lengths = []
        for r in records:
            content = self._extract_content(r, fmt)
            lengths.append(len(content))

        if len(lengths) < 2:
            return []

        mean_len = statistics.mean(lengths)
        median_len = statistics.median(lengths)
        stddev_len = statistics.stdev(lengths)

        # Zero variance
        if stddev_len == 0:
            findings.append(StageFinding(
                stage="ai_safety",
                severity="low",
                code="training_zero_variance",
                message="All records have identical content length.",
                details={"length": lengths[0], "count": len(lengths)},
            ))

        # Extreme spread
        if mean_len > 0 and stddev_len > 5 * mean_len:
            findings.append(StageFinding(
                stage="ai_safety",
                severity="low",
                code="training_extreme_spread",
                message=f"Extreme length spread: stddev ({stddev_len:.0f}) > 5x mean ({mean_len:.0f}).",
                details={"mean": round(mean_len, 1), "stddev": round(stddev_len, 1), "median": round(median_len, 1)},
            ))

        # Short content
        short_count = sum(1 for l in lengths if l < 5)
        if short_count > 0:
            findings.append(StageFinding(
                stage="ai_safety",
                severity="low",
                code="training_short_content",
                message=f"{short_count} records have content shorter than 5 characters.",
                details={"count": short_count, "total": len(lengths)},
            ))

        # Length outliers (>10x median)
        if median_len > 0:
            outlier_count = sum(1 for l in lengths if l > 10 * median_len)
            if outlier_count > 0:
                findings.append(StageFinding(
                    stage="ai_safety",
                    severity="low",
                    code="training_length_outlier",
                    message=f"{outlier_count} records exceed 10x median length ({median_len:.0f}).",
                    details={"count": outlier_count, "median": round(median_len, 1)},
                ))

        return findings

    def _check_class_balance(self, records: list[dict], fmt: str) -> list[StageFinding]:
        if fmt not in ("chat", "instruction"):
            return []

        patterns = []
        for r in records:
            if fmt == "chat":
                messages = r.get("messages", [])
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("role") == "user":
                        content = msg.get("content", "")
                        # Use first 50 chars as pattern key
                        patterns.append(str(content)[:50])
                        break
            elif fmt == "instruction":
                inst = r.get("instruction", "")
                patterns.append(str(inst)[:50])

        if not patterns:
            return []

        counter = Counter(patterns)
        most_common_count = counter.most_common(1)[0][1]
        if most_common_count / len(patterns) > 0.90:
            return [StageFinding(
                stage="ai_safety",
                severity="medium",
                code="training_class_imbalance",
                message=f"Class imbalance: a single pattern covers {most_common_count}/{len(patterns)} records (>{90}%).",
                details={"dominant_count": most_common_count, "total": len(patterns)},
            )]
        return []
