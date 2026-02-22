"""Signature update and staleness tracking for ClamAV + YARA rules."""

import datetime
import json
import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger()


class SignatureManager:
    """Manages offline signature updates from USB bundles."""

    def __init__(self, clamav_dir: Path, yara_dir: Path, blacklist_path: Path):
        self._clamav_dir = clamav_dir
        self._yara_dir = yara_dir
        self._blacklist_path = blacklist_path

    def update_from_bundle(self, bundle_path: Path) -> dict:
        """Extract and install signature updates from a USB bundle directory.

        Expected bundle layout:
            bundle/
            ├── clamav/          # .cvd/.cld signature databases
            ├── yara_rules/      # .yar/.yara rule files
            └── blacklist.json   # SHA-256 hash blacklist

        Returns summary of what was updated.
        """
        result = {"clamav_updated": False, "yara_updated": False, "blacklist_updated": False}

        if not bundle_path.exists() or not bundle_path.is_dir():
            return result

        # ClamAV signatures
        clamav_bundle = bundle_path / "clamav"
        if clamav_bundle.exists():
            sig_files = list(clamav_bundle.glob("*.cvd")) + list(clamav_bundle.glob("*.cld"))
            if sig_files:
                self._clamav_dir.mkdir(parents=True, exist_ok=True)
                for sf in sig_files:
                    dest = self._clamav_dir / sf.name
                    shutil.copy2(sf, dest)
                result["clamav_updated"] = True
                result["clamav_files"] = len(sig_files)
                logger.info("clamav_sigs_updated", count=len(sig_files))

        # YARA rules
        yara_bundle = bundle_path / "yara_rules"
        if yara_bundle.exists():
            rule_files = list(yara_bundle.glob("*.yar")) + list(yara_bundle.glob("*.yara"))
            if rule_files:
                self._yara_dir.mkdir(parents=True, exist_ok=True)
                for rf in rule_files:
                    dest = self._yara_dir / rf.name
                    shutil.copy2(rf, dest)
                result["yara_updated"] = True
                result["yara_rules"] = len(rule_files)
                logger.info("yara_rules_updated", count=len(rule_files))

        # Blacklist
        bl_bundle = bundle_path / "blacklist.json"
        if bl_bundle.exists():
            try:
                # Validate JSON structure
                data = json.loads(bl_bundle.read_text())
                if "hashes" in data and isinstance(data["hashes"], list):
                    shutil.copy2(bl_bundle, self._blacklist_path)
                    result["blacklist_updated"] = True
                    result["blacklist_hashes"] = len(data["hashes"])
                    logger.info("blacklist_updated", count=len(data["hashes"]))
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("blacklist_update_failed", error=str(e))

        return result

    def get_freshness(self) -> dict:
        """Get staleness info for all signature sources."""
        now = datetime.datetime.utcnow()
        info = {}

        # ClamAV
        sig_files = list(self._clamav_dir.glob("*.cvd")) + list(self._clamav_dir.glob("*.cld")) if self._clamav_dir.exists() else []
        if sig_files:
            newest = max(sig_files, key=lambda p: p.stat().st_mtime)
            age_hours = (now - datetime.datetime.fromtimestamp(newest.stat().st_mtime)).total_seconds() / 3600
            info["clamav"] = {
                "freshness": self._classify_freshness(age_hours),
                "age_hours": round(age_hours, 1),
                "last_updated": datetime.datetime.fromtimestamp(newest.stat().st_mtime).isoformat(),
            }
        else:
            info["clamav"] = {"freshness": "missing", "age_hours": None, "last_updated": None}

        # YARA
        rule_files = list(self._yara_dir.glob("*.yar")) + list(self._yara_dir.glob("*.yara")) if self._yara_dir.exists() else []
        if rule_files:
            newest = max(rule_files, key=lambda p: p.stat().st_mtime)
            age_hours = (now - datetime.datetime.fromtimestamp(newest.stat().st_mtime)).total_seconds() / 3600
            info["yara"] = {
                "freshness": self._classify_freshness(age_hours),
                "age_hours": round(age_hours, 1),
                "last_updated": datetime.datetime.fromtimestamp(newest.stat().st_mtime).isoformat(),
            }
        else:
            info["yara"] = {"freshness": "missing", "age_hours": None, "last_updated": None}

        # Blacklist
        if self._blacklist_path.exists():
            age_hours = (now - datetime.datetime.fromtimestamp(self._blacklist_path.stat().st_mtime)).total_seconds() / 3600
            info["blacklist"] = {
                "freshness": self._classify_freshness(age_hours),
                "age_hours": round(age_hours, 1),
                "last_updated": datetime.datetime.fromtimestamp(self._blacklist_path.stat().st_mtime).isoformat(),
            }
        else:
            info["blacklist"] = {"freshness": "missing", "age_hours": None, "last_updated": None}

        return info

    @staticmethod
    def _classify_freshness(age_hours: float) -> str:
        """Color-coded freshness: fresh (<24h), stale (<7d), outdated (>7d)."""
        if age_hours < 24:
            return "fresh"
        elif age_hours < 168:  # 7 days
            return "stale"
        return "outdated"
