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
