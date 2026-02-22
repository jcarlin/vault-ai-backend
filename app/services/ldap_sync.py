import uuid

import structlog
from sqlalchemy import select

import app.core.database as db_module
from app.core.database import LdapGroupMapping, User

logger = structlog.get_logger()


class LdapSyncService:
    """Synchronize LDAP directory users/groups into the local database."""

    def __init__(self, ldap_service, session_factory=None, default_role: str = "user"):
        self._ldap = ldap_service
        self._session_factory = session_factory or db_module.async_session
        self._default_role = default_role

    async def _resolve_role(self, groups: list[str]) -> str:
        """Resolve vault role from LDAP group memberships."""
        if not groups:
            return self._default_role

        async with self._session_factory() as session:
            result = await session.execute(
                select(LdapGroupMapping).order_by(LdapGroupMapping.priority.desc())
            )
            mappings = list(result.scalars().all())

        for mapping in mappings:
            if mapping.ldap_group_dn in groups:
                return mapping.vault_role

        return self._default_role

    async def full_sync(self) -> dict:
        """Full sync: pull all LDAP users, create/update local records, deactivate removed."""
        ldap_users = await self._ldap.search_users()

        created = 0
        updated = 0
        deactivated = 0
        errors = []

        # Track which LDAP DNs we see (to deactivate missing ones)
        seen_dns = set()

        for ldap_user in ldap_users:
            dn = ldap_user.get("dn", "")
            if not dn:
                continue

            seen_dns.add(dn)

            try:
                email = ldap_user.get("email", "")
                name = ldap_user.get("name", ldap_user.get("username", ""))
                groups = ldap_user.get("groups", [])
                disabled = ldap_user.get("disabled", False)

                if not email:
                    email = f"{ldap_user.get('username', 'unknown')}@ldap.local"

                role = await self._resolve_role(groups)

                async with self._session_factory() as session:
                    # Check if user exists by ldap_dn
                    result = await session.execute(
                        select(User).where(User.ldap_dn == dn)
                    )
                    user = result.scalar_one_or_none()

                    if user:
                        # Update existing user
                        changed = False
                        if user.name != name:
                            user.name = name
                            changed = True
                        if user.email != email:
                            # Check email isn't taken by another user
                            dup = await session.execute(
                                select(User).where(User.email == email, User.id != user.id)
                            )
                            if not dup.scalar_one_or_none():
                                user.email = email
                                changed = True
                        if user.role != role:
                            user.role = role
                            changed = True
                        if disabled and user.status == "active":
                            user.status = "inactive"
                            changed = True
                        elif not disabled and user.status == "inactive":
                            user.status = "active"
                            changed = True

                        if changed:
                            await session.commit()
                            updated += 1
                    else:
                        # Also check by email
                        result = await session.execute(
                            select(User).where(User.email == email)
                        )
                        existing = result.scalar_one_or_none()

                        if existing:
                            # Link existing local user to LDAP
                            existing.ldap_dn = dn
                            existing.auth_source = "ldap"
                            existing.role = role
                            if disabled:
                                existing.status = "inactive"
                            await session.commit()
                            updated += 1
                        else:
                            # Create new user
                            new_user = User(
                                id=str(uuid.uuid4()),
                                name=name,
                                email=email,
                                role=role,
                                status="inactive" if disabled else "active",
                                ldap_dn=dn,
                                auth_source="ldap",
                            )
                            session.add(new_user)
                            await session.commit()
                            created += 1

            except Exception as e:
                errors.append(f"Error syncing {dn}: {e}")
                logger.warning("ldap_sync_user_error", dn=dn, error=str(e))

        # Deactivate LDAP users no longer in directory (never delete — audit trail)
        async with self._session_factory() as session:
            result = await session.execute(
                select(User).where(
                    User.auth_source == "ldap",
                    User.status == "active",
                )
            )
            ldap_users_in_db = list(result.scalars().all())

            for user in ldap_users_in_db:
                if user.ldap_dn and user.ldap_dn not in seen_dns:
                    user.status = "inactive"
                    deactivated += 1

            if deactivated > 0:
                await session.commit()

        logger.info(
            "ldap_sync_complete",
            created=created,
            updated=updated,
            deactivated=deactivated,
            errors=len(errors),
        )

        return {
            "success": len(errors) == 0,
            "users_created": created,
            "users_updated": updated,
            "users_deactivated": deactivated,
            "errors": errors,
        }
