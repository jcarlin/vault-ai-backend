import asyncio
import json
import platform
from datetime import datetime, timedelta, timezone

import structlog

from app.core.exceptions import VaultError

logger = structlog.get_logger()

# Services that can be managed
MANAGED_SERVICES = {"vault-vllm", "vault-api", "caddy", "prometheus", "grafana", "cockpit"}
RESTART_BLOCKED = {"vault-api"}  # Can't restart ourselves


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

    async def get_logs(
        self,
        service: str | None = None,
        severity: str | None = None,
        since: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Get system logs from journalctl. Returns (entries, total)."""
        if platform.system() != "Linux":
            return [], 0

        cmd = ["journalctl", "--output=json", "--no-pager"]
        if service:
            cmd.extend(["-u", service])
        if severity:
            priority_map = {"error": "3", "warning": "4", "info": "6", "debug": "7"}
            if severity.lower() in priority_map:
                cmd.extend(["-p", priority_map[severity.lower()]])
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
                    entry = json.loads(line)
                    entries.append(
                        {
                            "timestamp": entry.get("__REALTIME_TIMESTAMP", ""),
                            "service": entry.get("_SYSTEMD_UNIT", "unknown"),
                            "severity": entry.get("PRIORITY", "6"),
                            "message": entry.get("MESSAGE", ""),
                        }
                    )
                except json.JSONDecodeError:
                    continue

            total = len(entries)
            entries = entries[offset : offset + limit]
            return entries, total
        except Exception:
            return [], 0

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
