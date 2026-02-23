import asyncio
import json
import platform
import random
import shutil
from datetime import datetime, timedelta, timezone

import structlog

from app.core.exceptions import VaultError

logger = structlog.get_logger()

# Services that can be managed
MANAGED_SERVICES = {"vault-vllm", "vault-backend", "caddy", "prometheus", "grafana", "cockpit"}
RESTART_BLOCKED = {"vault-backend"}  # Can't restart ourselves

# Friendly name → systemd unit name (shared with WebSocket handler)
SERVICE_UNIT_MAP = {
    "vllm": "vault-vllm",
    "api-gateway": "vault-backend",
    "prometheus": "prometheus",
    "grafana": "grafana-server",
    "caddy": "caddy",
    "cockpit": "cockpit",
}

# journalctl PRIORITY → severity string
PRIORITY_TO_SEVERITY = {
    0: "critical",
    1: "critical",
    2: "critical",
    3: "error",
    4: "warning",
    5: "info",
    6: "info",
    7: "debug",
}


class ServiceManager:
    async def get_service_status(self, service_name: str) -> dict:
        """Get status of a single service via systemctl."""
        if platform.system() != "Linux":
            return {"name": service_name, "status": "unavailable", "uptime_seconds": None}

        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                "is-active",
                service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            status = stdout.decode().strip()
            is_active = status == "active"

            uptime = None
            if is_active:
                proc2 = await asyncio.create_subprocess_exec(
                    "systemctl",
                    "show",
                    service_name,
                    "--property=ActiveEnterTimestamp",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout2, _ = await proc2.communicate()
                line = stdout2.decode().strip()
                if "=" in line:
                    ts_str = line.split("=", 1)[1].strip()
                    if ts_str:
                        try:
                            uptime = 0
                        except Exception:
                            pass

            return {
                "name": service_name,
                "status": "running" if is_active else "stopped",
                "uptime_seconds": uptime,
            }
        except Exception:
            return {"name": service_name, "status": "unavailable", "uptime_seconds": None}

    async def list_services(self) -> list[dict]:
        """Get status of all managed services."""
        tasks = [self.get_service_status(name) for name in sorted(MANAGED_SERVICES)]
        return await asyncio.gather(*tasks)

    async def restart_service(self, service_name: str) -> dict:
        """Restart a named service. Refuses self-restart and unknown services."""
        if service_name not in MANAGED_SERVICES:
            raise VaultError(
                code="invalid_service", message=f"Unknown service: {service_name}", status=400
            )

        if service_name in RESTART_BLOCKED:
            raise VaultError(
                code="restart_blocked",
                message=f"Cannot restart {service_name} via API.",
                status=400,
            )

        if platform.system() != "Linux":
            return {
                "service": service_name,
                "status": "restart_skipped",
                "message": "Not running on Linux",
            }

        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                "restart",
                service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    "service_restart_failed", service=service_name, error=stderr.decode()
                )
                return {
                    "service": service_name,
                    "status": "failed",
                    "message": stderr.decode().strip(),
                }
            return {
                "service": service_name,
                "status": "restarting",
                "message": f"{service_name} restart initiated",
            }
        except Exception as e:
            return {"service": service_name, "status": "failed", "message": str(e)}

    @staticmethod
    def _format_utc(dt: datetime) -> str:
        """Format a UTC datetime as ISO 8601 with Z suffix."""
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def _parse_journal_entry(self, raw: dict) -> dict:
        """Transform raw journalctl JSON into frontend-compatible log entry."""
        # Timestamp: microseconds since epoch → ISO 8601
        ts_usec = raw.get("__REALTIME_TIMESTAMP")
        if ts_usec:
            try:
                ts = self._format_utc(
                    datetime.fromtimestamp(int(ts_usec) / 1_000_000, tz=timezone.utc)
                )
            except (ValueError, OSError):
                ts = self._format_utc(datetime.now(timezone.utc))
        else:
            ts = self._format_utc(datetime.now(timezone.utc))

        # Severity: priority int → string
        try:
            priority = int(raw.get("PRIORITY", 6))
        except (ValueError, TypeError):
            priority = 6
        severity = PRIORITY_TO_SEVERITY.get(priority, "info")

        # Service: strip .service suffix
        svc = raw.get("_SYSTEMD_UNIT", raw.get("SYSLOG_IDENTIFIER", "unknown"))
        if svc.endswith(".service"):
            svc = svc[:-8]

        return {
            "timestamp": ts,
            "service": svc,
            "severity": severity,
            "message": raw.get("MESSAGE", ""),
        }

    async def get_logs(
        self,
        service: str | None = None,
        severity: str | None = None,
        since: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Get system logs from journalctl. Returns (entries, total)."""
        if platform.system() != "Linux" or shutil.which("journalctl") is None:
            return self._mock_logs(service, severity, limit, offset)

        cmd = ["journalctl", "--output=json", "--no-pager", "--reverse"]
        if service:
            # Map friendly names to systemd unit names
            unit = SERVICE_UNIT_MAP.get(service, service)
            cmd.extend(["-u", unit])
        if severity:
            priority_map = {"error": "3", "warning": "4", "info": "6", "debug": "7"}
            pri = priority_map.get(severity.lower())
            if pri:
                cmd.extend(["-p", f"0..{pri}"])
        if since:
            cmd.extend(["--since", since])
        cmd.extend(["-n", str(limit + offset)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()

            entries = []
            for line in stdout.decode().strip().split("\n"):
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    entries.append(self._parse_journal_entry(raw))
                except json.JSONDecodeError:
                    continue

            total = len(entries)
            entries = entries[offset : offset + limit]
            return entries, total
        except Exception:
            return [], 0

    def _mock_logs(
        self,
        service: str | None,
        severity: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        """Return sample log entries on non-Linux (dev) for UI testing."""
        services = ["vault-backend", "vault-vllm", "caddy", "prometheus", "grafana"]
        severities = ["info", "info", "info", "info", "warning", "error", "debug"]
        messages = [
            "Request completed successfully",
            "Model qwen2.5-32b-awq loaded in 4.2s",
            "Health check passed — all services operational",
            "TLS certificate valid for 364 days",
            "Slow query detected: 1.8s on /v1/chat/completions",
            "Connection refused to vLLM backend — retrying in 5s",
            "Worker process started (PID 4821)",
            "Prometheus scrape completed — 142 metrics exported",
            "Rate limit threshold approaching for key vault_sk_a1b2",
            "Disk usage at 67% on /opt/vault/models",
            "Database vacuum completed — freed 12MB",
            "GPU temperature nominal: 52°C",
            "Caddy reverse proxy reloaded with new TLS config",
            "Backup job completed — 48MB archive created",
            "Inference request queued — 3 pending",
        ]

        # Use a seeded RNG so results are stable within a given second
        # but vary across time (for realistic-looking refreshes)
        seed = int(datetime.now(timezone.utc).timestamp())
        rng = random.Random(seed)

        now = datetime.now(timezone.utc)
        pool = []
        for i in range(200):
            svc = rng.choice(services)
            sev = rng.choice(severities)
            if service and svc != SERVICE_UNIT_MAP.get(service, service):
                continue
            if severity and sev != severity.lower():
                continue
            ts = (now - timedelta(seconds=i * 15)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            pool.append({
                "timestamp": ts,
                "service": svc,
                "severity": sev,
                "message": rng.choice(messages),
            })

        total = len(pool)
        return pool[offset : offset + limit], total

    async def get_inference_stats(self, session_factory) -> dict:
        """Calculate inference stats from AuditLog for last 5 minutes."""
        from sqlalchemy import select

        from app.core.database import AuditLog

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)

        async with session_factory() as session:
            result = await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "http_request",
                    AuditLog.path.like("%/chat/completions%"),
                    AuditLog.timestamp >= cutoff,
                )
            )
            rows = list(result.scalars().all())

        if not rows:
            return {
                "requests_per_minute": 0.0,
                "avg_latency_ms": 0.0,
                "tokens_per_second": 0.0,
                "active_requests": 0,
                "window_seconds": 300,
            }

        total_requests = len(rows)
        avg_latency = sum(r.latency_ms or 0 for r in rows) / total_requests
        total_tokens = sum((r.tokens_output or 0) for r in rows)

        return {
            "requests_per_minute": round(total_requests / 5.0, 2),
            "avg_latency_ms": round(avg_latency, 1),
            "tokens_per_second": round(total_tokens / 300.0, 2),
            "active_requests": 0,
            "window_seconds": 300,
        }

    async def get_expanded_health(self, backend=None) -> dict:
        """Expanded health check covering all services."""
        services = await self.list_services()

        vllm_healthy = False
        if backend:
            try:
                vllm_healthy = await backend.health_check()
            except Exception:
                pass

        for svc in services:
            if svc["name"] == "vault-vllm" and vllm_healthy:
                svc["status"] = "running"

        running_count = sum(1 for s in services if s["status"] == "running")
        total = len(services)

        if running_count == total:
            overall = "healthy"
        elif running_count > 0:
            overall = "degraded"
        else:
            overall = "unhealthy"

        return {
            "status": overall,
            "services": services,
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        }
