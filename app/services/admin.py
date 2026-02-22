import socket
import uuid

from sqlalchemy import select, update

import app.core.database as db_module
from app.core.database import ApiKey, SystemConfig, User
from app.core.exceptions import NotFoundError, VaultError
from app.services.auth import AuthService


# ── Defaults ────────────────────────────────────────────────────────────────

NETWORK_DEFAULTS = {
    "network.hostname": "vault-cube",
    "network.subnet_mask": "255.255.255.0",
    "network.gateway": "192.168.1.1",
    "network.dns_servers": '["8.8.8.8","8.8.4.4"]',
    "network.network_mode": "lan",
}

SYSTEM_DEFAULTS = {
    "system.timezone": "UTC",
    "system.language": "en",
    "system.auto_update": "false",
    "system.telemetry": "false",
    "system.session_timeout": "3600",
    "system.max_upload_size": "1073741824",
    "system.debug_logging": "false",
    "system.diagnostics_enabled": "true",
}

QUARANTINE_DEFAULTS = {
    "quarantine.max_file_size": "1073741824",
    "quarantine.max_batch_files": "100",
    "quarantine.max_compression_ratio": "100",
    "quarantine.max_archive_depth": "3",
    "quarantine.auto_approve_clean": "true",
    "quarantine.strictness_level": "standard",
}


class AdminService:
    def __init__(self, session_factory=None):
        self._session_factory = session_factory or db_module.async_session
        self._auth_service = AuthService(session_factory=self._session_factory)

    # ── Users ───────────────────────────────────────────────────────────────

    async def list_users(self) -> list[User]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(User).order_by(User.created_at.desc())
            )
            return list(result.scalars().all())

    async def create_user(self, name: str, email: str, role: str = "user") -> User:
        async with self._session_factory() as session:
            # Check for duplicate email
            existing = await session.execute(
                select(User).where(User.email == email)
            )
            if existing.scalar_one_or_none() is not None:
                raise VaultError(
                    code="duplicate_email",
                    message=f"A user with email '{email}' already exists.",
                    status=409,
                )

            user = User(
                id=str(uuid.uuid4()),
                name=name,
                email=email,
                role=role,
                status="active",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def update_user(self, user_id: str, **updates) -> User:
        async with self._session_factory() as session:
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                raise NotFoundError(f"User '{user_id}' not found.")

            # Check duplicate email if email is being changed
            if "email" in updates and updates["email"] is not None and updates["email"] != user.email:
                dup = await session.execute(
                    select(User).where(User.email == updates["email"])
                )
                if dup.scalar_one_or_none() is not None:
                    raise VaultError(
                        code="duplicate_email",
                        message=f"A user with email '{updates['email']}' already exists.",
                        status=409,
                    )

            for field, value in updates.items():
                if value is not None:
                    setattr(user, field, value)

            await session.commit()
            await session.refresh(user)
            return user

    async def deactivate_user(self, user_id: str) -> User:
        async with self._session_factory() as session:
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                raise NotFoundError(f"User '{user_id}' not found.")

            user.status = "inactive"
            await session.commit()
            await session.refresh(user)
            return user

    # ── API Keys (delegates to AuthService) ─────────────────────────────────

    async def list_keys(self) -> list[ApiKey]:
        return await self._auth_service.list_keys()

    async def create_key(self, label: str, scope: str = "user", notes: str | None = None) -> tuple[str, ApiKey]:
        return await self._auth_service.create_key(label=label, scope=scope, notes=notes)

    async def update_key_by_id(self, key_id: int, **updates) -> ApiKey:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApiKey).where(ApiKey.id == key_id)
            )
            key_row = result.scalar_one_or_none()
            if key_row is None:
                raise NotFoundError(f"API key with id {key_id} not found.")

            for field, value in updates.items():
                if value is not None:
                    setattr(key_row, field, value)

            await session.commit()
            await session.refresh(key_row)
            return key_row

    async def revoke_key_by_id(self, key_id: int) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApiKey).where(ApiKey.id == key_id, ApiKey.is_active == True)  # noqa: E712
            )
            key_row = result.scalar_one_or_none()
            if key_row is None:
                raise NotFoundError(f"API key with id {key_id} not found.")

            key_row.is_active = False
            await session.commit()
            return True

    # ── Network Config ──────────────────────────────────────────────────────

    async def get_network_config(self) -> dict:
        async with self._session_factory() as session:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key.startswith("network."))
            )
            rows = {r.key: r.value for r in result.scalars().all()}

        # Populate defaults for any missing keys
        if not rows:
            await self._populate_defaults(NETWORK_DEFAULTS)
            rows = dict(NETWORK_DEFAULTS)

        # Resolve ip_address dynamically if not stored
        ip_address = rows.get("network.ip_address")
        if not ip_address:
            try:
                ip_address = socket.gethostbyname(socket.gethostname())
            except Exception:
                ip_address = "127.0.0.1"

        import json
        dns_raw = rows.get("network.dns_servers", '["8.8.8.8","8.8.4.4"]')
        try:
            dns_servers = json.loads(dns_raw)
        except (json.JSONDecodeError, TypeError):
            dns_servers = ["8.8.8.8", "8.8.4.4"]

        return {
            "hostname": rows.get("network.hostname", "vault-cube"),
            "ip_address": ip_address,
            "subnet_mask": rows.get("network.subnet_mask", "255.255.255.0"),
            "gateway": rows.get("network.gateway", "192.168.1.1"),
            "dns_servers": dns_servers,
            "network_mode": rows.get("network.network_mode", "lan"),
        }

    async def update_network_config(self, **updates) -> dict:
        import json

        async with self._session_factory() as session:
            for field, value in updates.items():
                if value is None:
                    continue
                key = f"network.{field}"
                stored_value = json.dumps(value) if isinstance(value, list) else str(value)

                existing = await session.execute(
                    select(SystemConfig).where(SystemConfig.key == key)
                )
                row = existing.scalar_one_or_none()
                if row:
                    row.value = stored_value
                else:
                    session.add(SystemConfig(key=key, value=stored_value))
            await session.commit()

        return await self.get_network_config()

    # ── System Settings ─────────────────────────────────────────────────────

    async def get_system_settings(self) -> dict:
        async with self._session_factory() as session:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key.startswith("system."))
            )
            rows = {r.key: r.value for r in result.scalars().all()}

        if not rows:
            await self._populate_defaults(SYSTEM_DEFAULTS)
            rows = dict(SYSTEM_DEFAULTS)

        return {
            "timezone": rows.get("system.timezone", "UTC"),
            "language": rows.get("system.language", "en"),
            "auto_update": rows.get("system.auto_update", "false").lower() == "true",
            "telemetry": rows.get("system.telemetry", "false").lower() == "true",
            "session_timeout": int(rows.get("system.session_timeout", "3600")),
            "max_upload_size": int(rows.get("system.max_upload_size", "1073741824")),
            "debug_logging": rows.get("system.debug_logging", "false").lower() == "true",
            "diagnostics_enabled": rows.get("system.diagnostics_enabled", "true").lower() == "true",
        }

    async def update_system_settings(self, **updates) -> dict:
        async with self._session_factory() as session:
            for field, value in updates.items():
                if value is None:
                    continue
                key = f"system.{field}"
                if isinstance(value, bool):
                    stored_value = "true" if value else "false"
                else:
                    stored_value = str(value)

                existing = await session.execute(
                    select(SystemConfig).where(SystemConfig.key == key)
                )
                row = existing.scalar_one_or_none()
                if row:
                    row.value = stored_value
                else:
                    session.add(SystemConfig(key=key, value=stored_value))
            await session.commit()

        return await self.get_system_settings()

    # ── Full Config ──────────────────────────────────────────────────────

    async def get_full_config(self) -> dict:
        """Merge network + system + TLS config."""
        network = await self.get_network_config()
        system = await self.get_system_settings()
        tls = await self.get_tls_info()
        return {"network": network, "system": system, "tls": tls, "restart_required": False}

    async def update_full_config(self, updates: dict) -> dict:
        """Partial update across config sections."""
        restart_required = False
        if "network" in updates and updates["network"]:
            network_updates = updates["network"]
            if isinstance(network_updates, dict):
                if "hostname" in network_updates:
                    restart_required = True
                await self.update_network_config(**network_updates)
        if "system" in updates and updates["system"]:
            system_updates = updates["system"]
            if isinstance(system_updates, dict):
                await self.update_system_settings(**system_updates)
        result = await self.get_full_config()
        result["restart_required"] = restart_required
        return result

    # ── TLS ──────────────────────────────────────────────────────────────

    async def get_tls_info(self) -> dict:
        """Get TLS certificate info."""
        from pathlib import Path
        from app.config import settings

        cert_dir = Path(settings.vault_tls_cert_dir)
        cert_path = cert_dir / "cert.pem"
        if not cert_path.exists():
            return {"enabled": False, "self_signed": True, "issuer": None, "expires": None, "serial": None}

        return {
            "enabled": True,
            "self_signed": True,
            "issuer": "Vault AI (self-signed)",
            "expires": None,
            "serial": None,
        }

    async def upload_tls_cert(self, certificate: str, private_key: str) -> dict:
        """Validate and write TLS cert to disk."""
        from pathlib import Path
        from app.config import settings

        if "-----BEGIN CERTIFICATE-----" not in certificate:
            raise VaultError(code="validation_error", message="Invalid certificate: must be PEM format.", status=400)
        if "-----BEGIN" not in private_key:
            raise VaultError(code="validation_error", message="Invalid private key: must be PEM format.", status=400)

        cert_dir = Path(settings.vault_tls_cert_dir)
        cert_dir.mkdir(parents=True, exist_ok=True)
        (cert_dir / "cert.pem").write_text(certificate)
        (cert_dir / "key.pem").write_text(private_key)

        return await self.get_tls_info()

    # ── Helpers ─────────────────────────────────────────────────────────────

    async def _populate_defaults(self, defaults: dict) -> None:
        async with self._session_factory() as session:
            for key, value in defaults.items():
                existing = await session.execute(
                    select(SystemConfig).where(SystemConfig.key == key)
                )
                if existing.scalar_one_or_none() is None:
                    session.add(SystemConfig(key=key, value=value))
            await session.commit()
