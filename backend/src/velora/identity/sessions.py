"""Magic-link login and device sessions (P3-T2, P3-T3, AD-23).

- A magic link is single-use, lives 15 minutes or less (also a database CHECK), and logs in
  exactly the staff member it was issued for.
- A device session ends after 12 hours idle or 30 days in total, on logout, on "log out all
  devices", or when the staff member is removed or their role changes.
- Every expiry decision uses the injected `Clock`, never the database's `now()`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from psycopg import AsyncConnection

from velora.clock import Clock
from velora.db import Database, TenantId
from velora.identity import audit
from velora.identity.errors import expect_row
from velora.identity.permissions import Permission, Role, allows
from velora.identity.tokens import ScopedToken, new_token, parse_token

MAGIC_LINK_TTL = timedelta(minutes=15)
SESSION_IDLE_TIMEOUT = timedelta(hours=12)
SESSION_ABSOLUTE_LIFETIME = timedelta(days=30)
# last_seen_at is refreshed at most this often, to avoid a write on every request.
LAST_SEEN_RESOLUTION = timedelta(minutes=1)

LinkPurpose = Literal["invite", "login"]


@dataclass(frozen=True, slots=True)
class StaffSession:
    """An authenticated device session. `tenant` comes from the session row, not the client."""

    tenant: TenantId
    session_id: uuid.UUID
    staff_id: uuid.UUID
    role: Role
    display_name: str
    token: ScopedToken

    def can(self, permission: Permission) -> bool:
        return allows(self.role, permission)


@dataclass(frozen=True, slots=True)
class IssuedLink:
    link_id: uuid.UUID
    token: ScopedToken
    expires_at: datetime


async def issue_magic_link(
    conn: AsyncConnection[Any],
    tenant: TenantId,
    staff_id: uuid.UUID,
    *,
    purpose: LinkPurpose,
    issued_by_staff_id: uuid.UUID,
    now: datetime,
) -> IssuedLink:
    """Insert a magic link for `staff_id` inside the caller's tenant transaction."""
    token = new_token(tenant)
    expires_at = now + MAGIC_LINK_TTL
    cur = await conn.execute(
        "INSERT INTO velora.magic_links (tenant_id, staff_member_id, token_hash, purpose, "
        "issued_by_kind, issued_by_staff_id, created_at, expires_at) "
        "VALUES (%s, %s, %s, %s, 'staff', %s, %s, %s) RETURNING id",
        (tenant.value, staff_id, token.hash, purpose, issued_by_staff_id, now, expires_at),
    )
    row = expect_row(await cur.fetchone())
    return IssuedLink(row[0], token, expires_at)


async def revoke_staff_sessions(
    conn: AsyncConnection[Any], staff_id: uuid.UUID, *, reason: str, now: datetime
) -> int:
    """Revoke every live session of one staff member. Returns how many were revoked."""
    cur = await conn.execute(
        "UPDATE velora.staff_sessions SET revoked_at = %s, revoked_reason = %s "
        "WHERE staff_member_id = %s AND revoked_at IS NULL",
        (now, reason, staff_id),
    )
    return cur.rowcount


class SessionService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def login(self, raw_link: str, *, device_label: str | None = None) -> StaffSession | None:
        """Exchange a magic link for a new device session, or None if it is not usable."""
        link = parse_token(raw_link)
        if link is None:
            return None
        now = self._clock.now()
        async with self._db.tenant(link.tenant) as conn:
            # Consuming and checking happen in one statement, so a link can't be used twice
            # even by two concurrent requests.
            cur = await conn.execute(
                "UPDATE velora.magic_links SET consumed_at = %(now)s "
                "WHERE token_hash = %(hash)s AND consumed_at IS NULL AND expires_at > %(now)s "
                "RETURNING id, staff_member_id, purpose",
                {"now": now, "hash": link.hash},
            )
            row = await cur.fetchone()
            if row is None:
                await self._audit_failed_link(conn, link, now)
                return None
            link_id, staff_id, purpose = row

            cur = await conn.execute(
                "SELECT role, display_name, active FROM velora.staff_members WHERE id = %s",
                (staff_id,),
            )
            role, display_name, active = expect_row(await cur.fetchone())
            if not active:
                await self._audit_login_failure(
                    conn, link.tenant, staff_id, link_id, "removed", now
                )
                return None

            session = new_token(link.tenant)
            cur = await conn.execute(
                "INSERT INTO velora.staff_sessions (tenant_id, staff_member_id, token_hash, "
                "device_label, created_at, last_seen_at, absolute_expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    link.tenant.value,
                    staff_id,
                    session.hash,
                    device_label,
                    now,
                    now,
                    now + SESSION_ABSOLUTE_LIFETIME,
                ),
            )
            created = expect_row(await cur.fetchone())
            await audit.record(
                conn,
                link.tenant,
                at=now,
                actor_kind="staff",
                actor_id=staff_id,
                action="auth.login",
                target_type="staff_session",
                target_id=created[0],
                details={"magic_link_id": str(link_id), "purpose": purpose},
            )
        return StaffSession(link.tenant, created[0], staff_id, Role(role), display_name, session)

    async def _audit_failed_link(
        self, conn: AsyncConnection[Any], link: ScopedToken, now: datetime
    ) -> None:
        """Audit a reused or expired link. Unknown tokens are not audited: their tenant part
        is unverified and may not even exist."""
        cur = await conn.execute(
            "SELECT id, staff_member_id, consumed_at FROM velora.magic_links WHERE token_hash = %s",
            (link.hash,),
        )
        known = await cur.fetchone()
        if known is None:
            return
        link_id, staff_id, consumed_at = known
        reason = "reused" if consumed_at is not None else "expired"
        await self._audit_login_failure(conn, link.tenant, staff_id, link_id, reason, now)

    async def _audit_login_failure(
        self,
        conn: AsyncConnection[Any],
        tenant: TenantId,
        staff_id: uuid.UUID,
        link_id: uuid.UUID,
        reason: str,
        now: datetime,
    ) -> None:
        await audit.record(
            conn,
            tenant,
            at=now,
            actor_kind="staff",
            actor_id=staff_id,
            action="auth.login_failed",
            target_type="magic_link",
            target_id=link_id,
            details={"reason": reason},
        )

    async def authenticate(self, raw_session: str | None) -> StaffSession | None:
        """The live session for a session cookie, or None. Expired sessions are revoked."""
        token = parse_token(raw_session)
        if token is None:
            return None
        now = self._clock.now()
        async with self._db.tenant(token.tenant) as conn:
            cur = await conn.execute(
                "SELECT s.id, s.staff_member_id, s.last_seen_at, s.absolute_expires_at, "
                "s.revoked_at, m.role, m.display_name, m.active "
                "FROM velora.staff_sessions s JOIN velora.staff_members m "
                "ON m.tenant_id = s.tenant_id AND m.id = s.staff_member_id "
                "WHERE s.token_hash = %s",
                (token.hash,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            session_id, staff_id, last_seen, absolute_expiry, revoked_at, role, name, active = row
            if revoked_at is not None:
                return None

            ended = None
            if not active:
                ended = "staff_removed"
            elif now >= absolute_expiry:
                ended = "expired_absolute"
            elif now - last_seen >= SESSION_IDLE_TIMEOUT:
                ended = "expired_idle"
            if ended is not None:
                await conn.execute(
                    "UPDATE velora.staff_sessions SET revoked_at = %s, revoked_reason = %s "
                    "WHERE id = %s AND revoked_at IS NULL",
                    (now, ended, session_id),
                )
                return None

            if now - last_seen >= LAST_SEEN_RESOLUTION:
                await conn.execute(
                    "UPDATE velora.staff_sessions SET last_seen_at = %s WHERE id = %s",
                    (now, session_id),
                )
        return StaffSession(token.tenant, session_id, staff_id, Role(role), name, token)

    async def logout(self, session: StaffSession) -> None:
        now = self._clock.now()
        async with self._db.tenant(session.tenant) as conn:
            await conn.execute(
                "UPDATE velora.staff_sessions SET revoked_at = %s, revoked_reason = 'logout' "
                "WHERE id = %s AND revoked_at IS NULL",
                (now, session.session_id),
            )
            await audit.record(
                conn,
                session.tenant,
                at=now,
                actor_kind="staff",
                actor_id=session.staff_id,
                action="auth.logout",
                target_type="staff_session",
                target_id=session.session_id,
            )

    async def logout_all(self, session: StaffSession) -> int:
        """Log the signed-in staff member out on every device, this one included."""
        now = self._clock.now()
        async with self._db.tenant(session.tenant) as conn:
            count = await revoke_staff_sessions(
                conn, session.staff_id, reason="logout_all", now=now
            )
            await audit.record(
                conn,
                session.tenant,
                at=now,
                actor_kind="staff",
                actor_id=session.staff_id,
                action="auth.logout_all",
                target_type="staff_member",
                target_id=session.staff_id,
                details={"sessions_revoked": count},
            )
        return count
