"""YARA rule loader and scanner.

Uses yara-x (Rust-based YARA successor) if available, falls back to yara-python,
or gracefully degrades if neither is installed.
"""

from pathlib import Path

import structlog

logger = structlog.get_logger()


class YaraEngine:
    """Load and apply YARA rules from a directory."""

    def __init__(self, rules_dir: str | None = None):
        self._rules_dir = Path(rules_dir) if rules_dir else None
        self._compiled_rules = None
        self._engine_type: str | None = None
        self._available = False
        self._load_error: str | None = None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def engine_type(self) -> str | None:
        return self._engine_type

    def load_rules(self) -> bool:
        """Load and compile YARA rules from the rules directory.

        Returns True if rules were loaded successfully, False otherwise.
        """
        if not self._rules_dir or not self._rules_dir.exists():
            self._load_error = "Rules directory not found"
            return False

        rule_files = list(self._rules_dir.glob("*.yar")) + list(self._rules_dir.glob("*.yara"))
        if not rule_files:
            self._load_error = "No .yar/.yara rule files found"
            return False

        # Try yara-x first (Rust-based, preferred)
        if self._try_load_yara_x(rule_files):
            return True

        # Fall back to yara-python
        if self._try_load_yara_python(rule_files):
            return True

        self._load_error = "Neither yara-x nor yara-python available"
        logger.info("yara_not_available", message=self._load_error)
        return False

    def scan_file(self, file_path: Path) -> list[dict]:
        """Scan a file against loaded YARA rules.

        Returns list of matches: [{"rule": "...", "tags": [...], "meta": {...}}]
        """
        if not self._available or self._compiled_rules is None:
            return []

        try:
            if self._engine_type == "yara-x":
                return self._scan_yara_x(file_path)
            elif self._engine_type == "yara-python":
                return self._scan_yara_python(file_path)
        except Exception as e:
            logger.warning("yara_scan_error", error=str(e), file=str(file_path))
        return []

    def _try_load_yara_x(self, rule_files: list[Path]) -> bool:
        """Try to load rules using yara-x."""
        try:
            import yara_x

            compiler = yara_x.Compiler()
            for rf in rule_files:
                source = rf.read_text(encoding="utf-8")
                compiler.add_source(source)
            self._compiled_rules = compiler.build()
            self._engine_type = "yara-x"
            self._available = True
            logger.info("yara_x_loaded", rule_count=len(rule_files))
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning("yara_x_load_error", error=str(e))
            return False

    def _try_load_yara_python(self, rule_files: list[Path]) -> bool:
        """Try to load rules using yara-python (legacy)."""
        try:
            import yara

            sources = {}
            for rf in rule_files:
                sources[rf.stem] = rf.read_text(encoding="utf-8")
            self._compiled_rules = yara.compile(sources=sources)
            self._engine_type = "yara-python"
            self._available = True
            logger.info("yara_python_loaded", rule_count=len(rule_files))
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning("yara_python_load_error", error=str(e))
            return False

    def _scan_yara_x(self, file_path: Path) -> list[dict]:
        """Scan with yara-x."""
        data = file_path.read_bytes()
        results = self._compiled_rules.scan(data)
        matches = []
        for rule in results.matching_rules:
            matches.append({
                "rule": rule.identifier,
                "tags": list(rule.tags) if hasattr(rule, "tags") else [],
                "meta": dict(rule.metadata) if hasattr(rule, "metadata") else {},
            })
        return matches

    def _scan_yara_python(self, file_path: Path) -> list[dict]:
        """Scan with yara-python."""
        matches = self._compiled_rules.match(str(file_path))
        return [
            {
                "rule": m.rule,
                "tags": list(m.tags),
                "meta": dict(m.meta),
            }
            for m in matches
        ]
