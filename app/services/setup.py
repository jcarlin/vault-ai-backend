import asyncio
import json
import time
from pathlib import Path

import structlog
from sqlalchemy import select

import app.core.database as db_module
from app.config import settings
from app.core.database import SystemConfig
from app.core.exceptions import VaultError
from app.schemas.setup import VerificationCheck
from app.services.admin import AdminService

logger = structlog.get_logger()

SETUP_STEPS = ["network", "admin", "sso", "tls", "model"]
REQUIRED_SETUP_STEPS = ["network", "admin", "tls", "model"]  # sso is optional


class SetupService:
    def __init__(self, session_factory=None):
        self._session_factory = session_factory or db_module.async_session
        self._admin_service = AdminService(session_factory=self._session_factory)

    # ── State Management ─────────────────────────────────────────────────────

    async def get_status(self) -> dict:
        """Return current setup wizard state."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key.startswith("setup."))
            )
            rows = {r.key: r.value for r in result.scalars().all()}

        status = rows.get("setup.status", "pending")
        completed_raw = rows.get("setup.completed_steps", "[]")
        try:
            completed_steps = json.loads(completed_raw)
        except (json.JSONDecodeError, TypeError):
            completed_steps = []

        # Determine current step: first step in SETUP_STEPS not yet completed
        current_step = None
        if status != "complete":
            for step in SETUP_STEPS:
                if step not in completed_steps:
                    current_step = step
                    break

        return {
            "status": status,
            "completed_steps": completed_steps,
            "current_step": current_step,
        }

    async def _get_completed_steps(self) -> list[str]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == "setup.completed_steps")
            )
            row = result.scalar_one_or_none()
            if row is None:
                return []
            try:
                return json.loads(row.value)
            except (json.JSONDecodeError, TypeError):
                return []

    async def _mark_step_complete(self, step: str) -> None:
        completed = await self._get_completed_steps()
        if step not in completed:
            completed.append(step)

        async with self._session_factory() as session:
            # Update completed_steps
            await self._upsert_config(session, "setup.completed_steps", json.dumps(completed))

            # Update status to in_progress if still pending
            status_row = await session.execute(
                select(SystemConfig).where(SystemConfig.key == "setup.status")
            )
            existing = status_row.scalar_one_or_none()
            if existing is None or existing.value == "pending":
                await self._upsert_config(session, "setup.status", "in_progress")

            await session.commit()

    async def _require_setup_not_complete(self) -> None:
        status = await self.get_status()
        if status["status"] == "complete":
            raise VaultError(
                code="setup_already_complete",
                message="Setup has already been completed.",
                status=409,
            )

    async def _upsert_config(self, session, key: str, value: str) -> None:
        result = await session.execute(
            select(SystemConfig).where(SystemConfig.key == key)
        )
        row = result.scalar_one_or_none()
        if row:
            row.value = value
        else:
            session.add(SystemConfig(key=key, value=value))

    # ── Network Step ─────────────────────────────────────────────────────────

    async def configure_network(
        self,
        hostname: str,
        ip_mode: str = "dhcp",
        ip_address: str | None = None,
        subnet_mask: str | None = None,
        gateway: str | None = None,
        dns_servers: list[str] | None = None,
    ) -> dict:
        await self._require_setup_not_complete()

        # Persist to DB via AdminService
        network_updates = {"hostname": hostname}
        if dns_servers is not None:
            network_updates["dns_servers"] = dns_servers
        if ip_address:
            network_updates["ip_address"] = ip_address
        if subnet_mask:
            network_updates["subnet_mask"] = subnet_mask
        if gateway:
            network_updates["gateway"] = gateway

        await self._admin_service.update_network_config(**network_updates)

        # Store ip_mode in SystemConfig
        async with self._session_factory() as session:
            await self._upsert_config(session, "network.ip_mode", ip_mode)
            await session.commit()

        # Apply hostname via hostnamectl (no-op on dev)
        await self._apply_hostname(hostname)

        # Apply network via nmcli (no-op on dev)
        if ip_mode == "static" and ip_address:
            await self._apply_static_network(ip_address, subnet_mask, gateway, dns_servers)

        await self._mark_step_complete("network")
        return await self._admin_service.get_network_config()

    async def _apply_hostname(self, hostname: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                "hostnamectl", "set-hostname", hostname,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                logger.warning("hostnamectl_failed", stderr=stderr.decode().strip())
        except FileNotFoundError:
            logger.warning("hostnamectl_not_found", msg="Skipping hostname set (dev environment)")
        except Exception as e:
            logger.warning("hostnamectl_error", error=str(e))

    async def _apply_static_network(
        self,
        ip_address: str,
        subnet_mask: str | None,
        gateway: str | None,
        dns_servers: list[str] | None,
    ) -> None:
        try:
            # Build nmcli command for static IP
            args = [
                "nmcli", "connection", "modify", "Wired connection 1",
                "ipv4.method", "manual",
                "ipv4.addresses", f"{ip_address}/{subnet_mask or '24'}",
            ]
            if gateway:
                args.extend(["ipv4.gateway", gateway])
            if dns_servers:
                args.extend(["ipv4.dns", " ".join(dns_servers)])

            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                logger.warning("nmcli_modify_failed", stderr=stderr.decode().strip())
        except FileNotFoundError:
            logger.warning("nmcli_not_found", msg="Skipping network config (dev environment)")
        except Exception as e:
            logger.warning("nmcli_error", error=str(e))

    # ── Admin Step ───────────────────────────────────────────────────────────

    async def create_admin(self, name: str, email: str) -> dict:
        await self._require_setup_not_complete()

        # Create admin user
        user = await self._admin_service.create_user(name=name, email=email, role="admin")

        # Create admin API key
        raw_key, key_row = await self._admin_service.create_key(
            label=f"Admin key for {name}",
            scope="admin",
            notes="Created during first-boot setup",
        )

        await self._mark_step_complete("admin")
        return {
            "user_id": user.id,
            "api_key": raw_key,
            "key_prefix": key_row.key_prefix,
        }

    # ── SSO Step (optional — skippable) ─────────────────────────────────────

    async def configure_sso(
        self,
        enabled: bool = True,
        url: str = "",
        bind_dn: str = "",
        bind_password: str = "",
        user_search_base: str = "",
        group_search_base: str = "",
        user_search_filter: str = "(sAMAccountName={username})",
        use_ssl: bool = False,
        test_connection: bool = True,
    ) -> dict:
        await self._require_setup_not_complete()

        # Save LDAP config via AdminService
        await self._admin_service.update_ldap_config(
            enabled=enabled,
            url=url,
            bind_dn=bind_dn,
            bind_password=bind_password,
            user_search_base=user_search_base,
            group_search_base=group_search_base,
            user_search_filter=user_search_filter,
            use_ssl=use_ssl,
        )

        # Optionally test connection
        test_result = None
        if enabled and test_connection:
            from app.services.ldap_service import LdapService
            ldap_svc = LdapService(
                url=url,
                bind_dn=bind_dn,
                bind_password=bind_password,
                user_search_base=user_search_base,
                group_search_base=group_search_base,
                user_search_filter=user_search_filter,
                use_ssl=use_ssl,
            )
            test_result = await ldap_svc.test_connection()

        await self._mark_step_complete("sso")
        result = {"status": "configured", "enabled": enabled}
        if test_result:
            result["test"] = test_result
        return result

    async def skip_sso(self) -> dict:
        """Skip SSO configuration (LDAP is optional)."""
        await self._require_setup_not_complete()
        await self._mark_step_complete("sso")
        return {"status": "skipped"}

    # ── TLS Step ─────────────────────────────────────────────────────────────

    async def configure_tls(
        self,
        mode: str = "self_signed",
        certificate: str | None = None,
        private_key: str | None = None,
    ) -> dict:
        await self._require_setup_not_complete()

        cert_dir = Path(settings.vault_tls_cert_dir)

        if mode == "self_signed":
            await self._generate_self_signed_cert(cert_dir)
        elif mode == "custom":
            if not certificate or not private_key:
                raise VaultError(
                    code="missing_tls_data",
                    message="Custom TLS mode requires both certificate and private_key PEM strings.",
                    status=400,
                )
            await self._write_custom_cert(cert_dir, certificate, private_key)
        else:
            raise VaultError(
                code="invalid_tls_mode",
                message=f"Invalid TLS mode '{mode}'. Must be 'self_signed' or 'custom'.",
                status=400,
            )

        # Store TLS mode in config
        async with self._session_factory() as session:
            await self._upsert_config(session, "setup.tls_mode", mode)
            await session.commit()

        await self._mark_step_complete("tls")
        return {"mode": mode, "status": "configured"}

    async def _generate_self_signed_cert(self, cert_dir: Path) -> None:
        try:
            cert_dir.mkdir(parents=True, exist_ok=True)
            cert_path = cert_dir / "vault.crt"
            key_path = cert_dir / "vault.key"

            process = await asyncio.create_subprocess_exec(
                "openssl", "req", "-x509", "-newkey", "rsa:4096",
                "-keyout", str(key_path),
                "-out", str(cert_path),
                "-days", "365",
                "-nodes",
                "-subj", "/CN=vault-cube.local/O=Vault AI Systems",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                logger.warning("openssl_self_signed_failed", stderr=stderr.decode().strip())
            else:
                logger.info("tls_self_signed_generated", cert=str(cert_path))
        except FileNotFoundError:
            logger.warning("openssl_not_found", msg="Skipping TLS cert generation (dev environment)")
        except Exception as e:
            logger.warning("openssl_error", error=str(e))

    async def _write_custom_cert(self, cert_dir: Path, certificate: str, private_key: str) -> None:
        try:
            cert_dir.mkdir(parents=True, exist_ok=True)
            cert_path = cert_dir / "vault.crt"
            key_path = cert_dir / "vault.key"
            cert_path.write_text(certificate)
            key_path.write_text(private_key)
            logger.info("tls_custom_cert_written", cert=str(cert_path))
        except Exception as e:
            logger.warning("tls_write_error", error=str(e))
            raise VaultError(
                code="tls_write_failed",
                message=f"Failed to write TLS certificate files: {e}",
                status=500,
            )

    # ── Model Step ───────────────────────────────────────────────────────────

    async def select_model(self, model_id: str) -> dict:
        await self._require_setup_not_complete()

        # Verify model exists in manifest
        manifest_path = Path(settings.vault_models_manifest)
        available_models = []
        if manifest_path.exists():
            with open(manifest_path) as f:
                data = json.load(f)
                available_models = [m["id"] for m in data.get("models", [])]

        if available_models and model_id not in available_models:
            raise VaultError(
                code="model_not_found",
                message=f"Model '{model_id}' not found in manifest. Available: {', '.join(available_models)}",
                status=404,
            )

        # Store selection in SystemConfig
        async with self._session_factory() as session:
            await self._upsert_config(session, "setup.selected_model", model_id)
            await session.commit()

        await self._mark_step_complete("model")
        return {"model_id": model_id, "status": "selected"}

    # ── Verification Step ────────────────────────────────────────────────────

    async def run_verification(self, inference_backend) -> dict:
        checks: list[VerificationCheck] = []

        # Check 1: Database connectivity
        db_start = time.perf_counter()
        try:
            async with self._session_factory() as session:
                await session.execute(select(SystemConfig).limit(1))
            db_ms = round((time.perf_counter() - db_start) * 1000, 1)
            checks.append(VerificationCheck(
                name="database", passed=True, message="Database is accessible", latency_ms=db_ms
            ))
        except Exception as e:
            db_ms = round((time.perf_counter() - db_start) * 1000, 1)
            checks.append(VerificationCheck(
                name="database", passed=False, message=f"Database error: {e}", latency_ms=db_ms
            ))

        # Check 2: Inference backend health
        inf_start = time.perf_counter()
        try:
            healthy = await inference_backend.health_check()
            inf_ms = round((time.perf_counter() - inf_start) * 1000, 1)
            checks.append(VerificationCheck(
                name="inference", passed=healthy,
                message="Inference backend is healthy" if healthy else "Inference backend is not responding",
                latency_ms=inf_ms,
            ))
        except Exception as e:
            inf_ms = round((time.perf_counter() - inf_start) * 1000, 1)
            checks.append(VerificationCheck(
                name="inference", passed=False, message=f"Inference check failed: {e}", latency_ms=inf_ms
            ))

        # Check 3: GPU detection
        gpu_start = time.perf_counter()
        try:
            from app.services.monitoring import get_gpu_info
            gpus = await get_gpu_info()
            gpu_ms = round((time.perf_counter() - gpu_start) * 1000, 1)
            if gpus:
                checks.append(VerificationCheck(
                    name="gpu", passed=True,
                    message=f"Detected {len(gpus)} GPU(s): {', '.join(g.name for g in gpus)}",
                    latency_ms=gpu_ms,
                ))
            else:
                checks.append(VerificationCheck(
                    name="gpu", passed=False,
                    message="No GPUs detected (expected on dev, ok for testing)",
                    latency_ms=gpu_ms,
                ))
        except Exception as e:
            gpu_ms = round((time.perf_counter() - gpu_start) * 1000, 1)
            checks.append(VerificationCheck(
                name="gpu", passed=False, message=f"GPU detection error: {e}", latency_ms=gpu_ms
            ))

        # Check 4: TLS certificate
        tls_start = time.perf_counter()
        cert_path = Path(settings.vault_tls_cert_dir) / "vault.crt"
        tls_ms = round((time.perf_counter() - tls_start) * 1000, 1)
        if cert_path.exists():
            checks.append(VerificationCheck(
                name="tls", passed=True, message="TLS certificate found", latency_ms=tls_ms
            ))
        else:
            checks.append(VerificationCheck(
                name="tls", passed=False,
                message="TLS certificate not found (expected on dev)",
                latency_ms=tls_ms,
            ))

        overall = "pass" if all(c.passed for c in checks) else "fail"
        return {"status": overall, "checks": checks}

    # ── Complete Step ────────────────────────────────────────────────────────

    async def complete_setup(self) -> dict:
        await self._require_setup_not_complete()

        # Verify all required steps are done (sso is optional)
        completed = await self._get_completed_steps()
        missing = [s for s in REQUIRED_SETUP_STEPS if s not in completed]
        if missing:
            raise VaultError(
                code="setup_incomplete",
                message=f"Cannot complete setup. Missing steps: {', '.join(missing)}",
                status=400,
                details={"missing_steps": missing},
            )

        # Mark complete in DB
        async with self._session_factory() as session:
            await self._upsert_config(session, "setup.status", "complete")

            from datetime import datetime, timezone
            await self._upsert_config(
                session, "setup.completed_at", datetime.now(timezone.utc).isoformat()
            )
            await session.commit()

        # Write flag file (fast startup check)
        try:
            flag_path = Path(settings.vault_setup_flag_path)
            flag_path.parent.mkdir(parents=True, exist_ok=True)
            flag_path.write_text("1")
            logger.info("setup_flag_written", path=str(flag_path))
        except Exception as e:
            logger.warning("setup_flag_write_failed", error=str(e))

        return {"status": "complete", "message": "Setup completed successfully. All endpoints now require authentication."}
